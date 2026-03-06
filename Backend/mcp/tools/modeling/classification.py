"""
PyCaret MCP Tools

MCP-compatible wrappers for PyCaret classification training and inference.
Follows the same structure as the multi-agent scratchpad MCP tools.

Tools exposed:
  - pycaret_train_classifier   : Full model comparison, tuning, blending, and evaluation
  - pycaret_run_inference      : Load a saved model and predict on new data
"""

import json
import logging
import os
import time
from typing import Optional

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from .. import mcp
from ..helpers.workspace import WorkspacePath, base_on_workspace_dir


# ── Helpers ────────────────────────────────────────────────────────────────────

def _setup_logging(log_file: str, verbose: bool = True) -> None:
    """Configure root logger to write to file and stdout."""
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO if verbose else logging.WARNING)
    for h in logger.handlers[:]:
        logger.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh = logging.FileHandler(log_file)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)


def _csv_list(value: Optional[str]) -> Optional[list]:
    """Convert a comma-separated string to a list, or return None."""
    if value:
        return [v.strip() for v in value.split(",") if v.strip()]
    return None


# ── Input Models ───────────────────────────────────────────────────────────────

class TrainClassifierInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    input_path: WorkspacePath = Field(
        ..., description="Path to input CSV file with training data."
    )
    # output_dir: WorkspacePath = Field(
    #     ..., description="Directory where model artefacts and results will be saved."
    # )
    # target_column: str = Field(
    #     ..., description="Name of the target column for classification."
    # )
    experiment_name: Optional[str] = Field(
        default=None, description="Experiment label. Auto-generated from timestamp if omitted."
    )
    fold: int = Field(
        default=10, description="Number of cross-validation folds."
    )
    session_id: Optional[int] = Field(
        default=None, description="Random seed for reproducibility. Randomly chosen if omitted."
    )
    use_gpu: bool = Field(
        default=False, description="Enable GPU acceleration (requires cuml)."
    )
    fix_imbalance: bool = Field(
        default=True, description="Apply SMOTE-style balancing when classes are imbalanced."
    )
    data_split_stratify: bool = Field(
        default=True, description="Use stratified train/test splitting."
    )
    data_split_shuffle: bool = Field(
        default=True, description="Shuffle data before splitting."
    )
    preprocess: bool = Field(
        default=True, description="Apply PyCaret's built-in preprocessing pipeline."
    )
    ignore_features: Optional[str] = Field(
        default=None, description="Comma-separated column names to exclude from training."
    )
    numeric_features: Optional[str] = Field(
        default=None, description="Comma-separated list of columns to treat as numeric."
    )
    categorical_features: Optional[str] = Field(
        default=None, description="Comma-separated list of columns to treat as categorical."
    )
    date_features: Optional[str] = Field(
        default=None, description="Comma-separated list of columns to treat as dates."
    )
    n_select: int = Field(
        default=3, description="Number of top models to select for tuning and blending."
    )
    normalize: bool = Field(
        default=False, description="Normalize numeric features before training."
    )
    transformation: bool = Field(
        default=False, description="Apply power transformation to numeric features."
    )
    pca: bool = Field(
        default=False, description="Reduce dimensionality with PCA."
    )
    pca_components: Optional[float] = Field(
        default=None,
        description="PCA components: float 0–1 for variance ratio, int > 1 for exact count.",
    )
    include_models: Optional[str] = Field(
        default=None, description="Comma-separated PyCaret model IDs to include in comparison."
    )
    exclude_models: Optional[str] = Field(
        default=None, description="Comma-separated PyCaret model IDs to exclude from comparison."
    )
    ignore_gpu_errors: bool = Field(
        default=True, description="Fall back to CPU silently when GPU/cuml is unavailable."
    )


class RunInferenceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    input_path: WorkspacePath = Field(
        ..., description="Path to CSV file containing rows to predict."
    )
    model_path: WorkspacePath = Field(
        ..., description="Path to the saved PyCaret model (with or without .pkl extension)."
    )
    # output_dir: Optional[WorkspacePath] = Field(
    #     default=None,
    #     description="Directory to write prediction outputs. Defaults to the input file's directory.",
    # )
    ground_truth_column: Optional[str] = Field(
        default=None,
        description=(
            "Column name holding ground-truth labels. "
            "When supplied, accuracy, AUC, F1, and a confusion matrix are calculated."
        ),
    )
    prediction_filename: WorkspacePath = Field(
        default="predictions.csv",
        description="File name for the saved predictions CSV.",
    )
    verbose: bool = Field(
        default=True, description="Emit detailed log messages."
    )


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="pycaret_train_classifier",
    annotations={
        "title": "Train a PyCaret Classification Pipeline",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def pycaret_train_classifier(params: TrainClassifierInput) -> str:
    """Compare, tune, and blend classification models with PyCaret.

    Workflow:
      1. Load CSV data and call pycaret.classification.setup().
      2. Compare all (or a filtered set of) models and select the top n_select.
      3. Tune each selected model individually.
      4. Blend the tuned models (if more than one).
      5. For every model (tuned + blended) generate plots, SHAP interpretations,
         and holdout-set predictions.
      6. Save all artefacts under output_dir and return a structured result object.

    Args:
        params (TrainClassifierInput): Full training configuration – see field descriptions.

    Returns:
        str: JSON with keys:
            - status           : "success" or "error"
            - experiment_name  : str
            - output_dir       : str
            - final_model_path : path to the final saved model
            - individual_model_paths : {model_name: path}
            - model_test_predictions : {model_name: predictions_csv_path}
            - model_summary    : path to summary CSV
            - log_file         : path to the run log
            - created_files    : [(label, path), ...]
            - error_message    : present only when status == "error"
    """
    try:
        import random

        output_dir = base_on_workspace_dir('modeling')
        os.makedirs(output_dir, exist_ok=True)

        target_column = "target"

        log_file = os.path.join(output_dir, "pycaret_classification.log")
        _setup_logging(log_file)

        experiment_name = params.experiment_name or f"classification_exp_{time.strftime('%Y%m%d_%H%M%S')}"
        session_id = params.session_id if params.session_id is not None else random.randint(1, 10_000)

        logging.info(f"Experiment: {experiment_name} | input: {params.input_path} | target: {target_column}")

        # ── Import PyCaret ──────────────────────────────────────────────────
        try:
            from pycaret.classification import (
                blend_models, compare_models, interpret_model,
                plot_model, predict_model, pull, save_model, setup, tune_model,
            )
            import pycaret
            logging.info(f"PyCaret {getattr(pycaret, '__version__', 'unknown')} imported")
        except ImportError as exc:
            raise ImportError("PyCaret is required. Install with: pip install pycaret") from exc

        # ── Load data ───────────────────────────────────────────────────────
        data = pd.read_csv(params.input_path)
        logging.info(f"Data loaded: {data.shape[0]} rows × {data.shape[1]} cols")
        if target_column not in data.columns:
            raise ValueError(f"Target column '{target_column}' not found in data.")

        # ── GPU guard ───────────────────────────────────────────────────────
        use_gpu = params.use_gpu
        if use_gpu:
            try:
                import cuml  # noqa: F401
            except ImportError:
                if params.ignore_gpu_errors:
                    logging.warning("cuml not installed – falling back to CPU.")
                    use_gpu = False
                else:
                    raise ImportError("GPU requested but cuml is not installed.")

        # ── Build setup kwargs ──────────────────────────────────────────────
        setup_kwargs: dict = dict(
            data=data,
            target=target_column,
            session_id=session_id,
            experiment_name=experiment_name,
            fold=params.fold,
            use_gpu=use_gpu,
            fix_imbalance=params.fix_imbalance,
            preprocess=params.preprocess,
            data_split_stratify=params.data_split_stratify,
            data_split_shuffle=params.data_split_shuffle,
            log_experiment=False,
            normalize=params.normalize,
            transformation=params.transformation,
            pca=params.pca,
        )
        for key, value in [
            ("ignore_features", _csv_list(params.ignore_features)),
            ("numeric_features", _csv_list(params.numeric_features)),
            ("categorical_features", _csv_list(params.categorical_features)),
            ("date_features", _csv_list(params.date_features)),
            ("pca_components", params.pca_components),
        ]:
            if value is not None:
                setup_kwargs[key] = value

        # ── Run setup with graceful fallback ────────────────────────────────
        def _try_setup(kwargs):
            try:
                return setup(**kwargs)
            except TypeError as exc:
                bad = str(exc)
                if "unexpected keyword argument" in bad:
                    param = bad.split("'")[1]
                    logging.warning(f"Removing unsupported setup param '{param}' and retrying.")
                    kwargs.pop(param, None)
                    return setup(**kwargs)
                raise
            except Exception:
                minimal = {"data": data, "target": target_column, "session_id": session_id}
                logging.warning("Setup failed – retrying with minimal params.")
                return setup(**minimal)

        _try_setup(setup_kwargs)
        logging.info("PyCaret setup complete.")

        # ── Compare models ──────────────────────────────────────────────────
        compare_kwargs: dict = {"n_select": params.n_select}
        if params.include_models:
            compare_kwargs["include"] = _csv_list(params.include_models)
        if params.exclude_models:
            compare_kwargs["exclude"] = _csv_list(params.exclude_models)

        try:
            best = compare_models(**compare_kwargs)
        except Exception:
            logging.warning("compare_models failed with custom kwargs – retrying with defaults.")
            best = compare_models()

        if not isinstance(best, list):
            best = [best]
        logging.info(f"{len(best)} model(s) selected.")

        created_files: list = []

        compare_df = pull()
        compare_path = os.path.join(output_dir, "model_comparison_results.csv")
        compare_df.to_csv(compare_path, index=True)
        created_files.append(("Model Comparison Results", compare_path))

        # ── Inner helpers ───────────────────────────────────────────────────
        def _create_plots(model, plots_dir: str) -> list:
            plot_types = [
                "auc", "confusion_matrix", "class_report", "feature", "boundary",
                "pr", "error", "learning", "manifold", "calibration", "vc",
                "dimension", "feature_all", "parameter", "lift", "gain",
            ]
            results = []
            original_dir = os.getcwd()
            try:
                os.chdir(plots_dir)
                for pt in plot_types:
                    try:
                        plot_model(model, plot=pt, save=True)
                        fp = os.path.join(plots_dir, f"{pt}.png")
                        if os.path.exists(fp):
                            results.append((f"Plot: {pt}", fp))
                    except Exception as exc:
                        logging.warning(f"Plot '{pt}' skipped: {exc}")
            finally:
                os.chdir(original_dir)
            return results

        def _create_interpretations(model, plots_dir: str) -> list:
            results = []
            original_dir = os.getcwd()
            try:
                os.chdir(plots_dir)
                for plot_type, filename in [("summary", "SHAP Summary.png"), ("reason", "SHAP Reason Code.png")]:
                    try:
                        kwargs = {"plot": plot_type, "save": True}
                        if plot_type == "reason":
                            kwargs["observation"] = 1
                        interpret_model(model, **kwargs)
                        fp = os.path.join(plots_dir, filename)
                        if os.path.exists(fp):
                            results.append((f"Interpretation: {plot_type}", fp))
                    except Exception as exc:
                        logging.warning(f"Interpretation '{plot_type}' skipped: {exc}")
            finally:
                os.chdir(original_dir)
            return results

        def _eval_on_holdout(model, output_path: str, name: str) -> Optional[str]:
            try:
                preds = predict_model(model)
                preds.to_csv(output_path, index=False)
                logging.info(f"Holdout predictions for '{name}' → {output_path}")
                return output_path
            except Exception as exc:
                logging.error(f"Holdout eval failed for '{name}': {exc}")
                return None

        # ── Tune individual models ──────────────────────────────────────────
        models_dir = os.path.join(output_dir, "models")
        os.makedirs(models_dir, exist_ok=True)
        tuned_models, individual_model_paths, model_test_predictions = [], {}, {}

        for i, model in enumerate(best):
            name = f"tuned_model_{i + 1}"
            model_dir = os.path.join(models_dir, name)
            os.makedirs(model_dir, exist_ok=True)
            logging.info(f"Tuning {name} ({i + 1}/{len(best)})")

            try:
                tuned = tune_model(model)
                tuned_models.append(tuned)

                pull().to_csv(os.path.join(model_dir, f"{name}_results.csv"), index=True)
                created_files.append((f"{name} Results", os.path.join(model_dir, f"{name}_results.csv")))

                model_pkl = os.path.join(model_dir, name)
                try:
                    save_model(tuned, model_pkl)
                    individual_model_paths[name] = model_pkl
                    created_files.append((f"{name} Saved Model", model_pkl))
                except Exception as exc:
                    logging.error(f"Could not save {name}: {exc}")

                plots_dir = os.path.join(model_dir, "plots")
                os.makedirs(plots_dir, exist_ok=True)
                created_files.extend(_create_plots(tuned, plots_dir))
                created_files.extend(_create_interpretations(tuned, plots_dir))

                holdout_path = os.path.join(model_dir, f"holdout_predictions_{name}.csv")
                result = _eval_on_holdout(tuned, holdout_path, name)
                if result:
                    model_test_predictions[name] = result
                    created_files.append((f"{name} Holdout Predictions", result))

            except Exception as exc:
                logging.error(f"Failed to process {name}: {exc}")

        # ── Blend models ────────────────────────────────────────────────────
        blended_model = None
        if len(tuned_models) > 1:
            blend_dir = os.path.join(models_dir, "blended_model")
            os.makedirs(blend_dir, exist_ok=True)
            try:
                blended_model = blend_models(tuned_models)
                pull().to_csv(os.path.join(blend_dir, "blended_model_results.csv"), index=True)
                created_files.append(("Blended Model Results", os.path.join(blend_dir, "blended_model_results.csv")))

                blend_pkl = os.path.join(blend_dir, "blended_model")
                try:
                    save_model(blended_model, blend_pkl)
                    individual_model_paths["blended_model"] = blend_pkl
                    created_files.append(("Blended Model Saved Model", blend_pkl))
                except Exception as exc:
                    logging.error(f"Could not save blended model: {exc}")

                blend_plots_dir = os.path.join(blend_dir, "plots")
                os.makedirs(blend_plots_dir, exist_ok=True)
                created_files.extend(_create_plots(blended_model, blend_plots_dir))
                created_files.extend(_create_interpretations(blended_model, blend_plots_dir))

                holdout_path = os.path.join(blend_dir, "holdout_predictions_blended_model.csv")
                result = _eval_on_holdout(blended_model, holdout_path, "blended_model")
                if result:
                    model_test_predictions["blended_model"] = result
                    created_files.append(("Blended Model Holdout Predictions", result))

            except Exception as exc:
                logging.error(f"Blending failed: {exc}")

        final_model = blended_model or (tuned_models[0] if tuned_models else best[0])

        # ── Save final model ────────────────────────────────────────────────
        final_model_path = os.path.join(output_dir, f"{experiment_name}_final_model")
        try:
            save_model(final_model, final_model_path)
            created_files.append(("Final Model", final_model_path))
        except Exception as exc:
            logging.error(f"Could not save final model: {exc}")
            final_model_path = None

        # ── Summary CSV ─────────────────────────────────────────────────────
        summary_path = os.path.join(output_dir, "model_summary.csv")
        rows = []
        for idx, m in enumerate(tuned_models):
            n = f"tuned_model_{idx + 1}"
            rows.append({"Model": n, "Type": "Tuned",
                         "Model Path": individual_model_paths.get(n, ""),
                         "Holdout Predictions": model_test_predictions.get(n, "")})
        if blended_model:
            rows.append({"Model": "blended_model", "Type": "Blended",
                         "Model Path": individual_model_paths.get("blended_model", ""),
                         "Holdout Predictions": model_test_predictions.get("blended_model", "")})
        pd.DataFrame(rows).to_csv(summary_path, index=False)
        created_files.append(("Model Summary", summary_path))

        logging.info("Classification experiment complete.")
        return json.dumps({
            "status": "success",
            "experiment_name": experiment_name,
            "output_dir": output_dir,
            "final_model_path": final_model_path,
            "individual_model_paths": individual_model_paths,
            "model_test_predictions": model_test_predictions,
            "model_summary": summary_path,
            "log_file": log_file,
            "created_files": created_files,
        }, indent=2)

    except Exception as exc:
        logging.error(f"Unhandled error in pycaret_train_classifier: {exc}", exc_info=True)
        return json.dumps({
            "status": "error",
            "error_message": str(exc),
            "input_path": params.input_path,
            "output_dir": output_dir,
        }, indent=2)


@mcp.tool(
    name="pycaret_run_inference",
    annotations={
        "title": "Run Inference with a Saved PyCaret Model",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def pycaret_run_inference(params: RunInferenceInput) -> str:
    """Load a saved PyCaret model and generate predictions on new data.

    Optionally computes performance metrics (accuracy, AUC, precision, recall,
    F1, specificity, confusion matrix) when a ground-truth column is provided.

    Args:
        params (RunInferenceInput): Inference configuration – see field descriptions.

    Returns:
        str: JSON with keys:
            - status                : "success" or "error"
            - predictions_path      : path to the predictions CSV
            - num_predictions       : number of rows predicted
            - has_ground_truth      : bool
            - metrics               : dict of computed metrics (if ground_truth_column given)
            - metrics_csv_path      : path to metrics CSV
            - metrics_json_path     : path to metrics JSON
            - comparison_path       : path to prediction-vs-actual CSV
            - confusion_matrix_path : path to confusion-matrix CSV
            - log_file              : path to the run log
            - error_message         : present only when status == "error"
    """
    try:
        import numpy as np
        from sklearn.metrics import (
            accuracy_score, confusion_matrix, f1_score,
            precision_score, recall_score, roc_auc_score,
        )

        output_dir = base_on_workspace_dir('modeling/output')
        output_dir = output_dir or os.path.dirname(params.input_path) or "."
        os.makedirs(output_dir, exist_ok=True)
        log_file = os.path.join(output_dir, "pycaret_inference.log")
        _setup_logging(log_file, verbose=params.verbose)

        logging.info(f"Inference | model: {params.model_path} | input: {params.input_path}")

        # ── Import PyCaret ──────────────────────────────────────────────────
        try:
            from pycaret.classification import load_model, predict_model
        except ImportError as exc:
            raise ImportError("PyCaret is required. Install with: pip install pycaret") from exc

        # ── Load data ───────────────────────────────────────────────────────
        data = pd.read_csv(params.input_path)
        logging.info(f"Data loaded: {data.shape}")

        # ── Preserve ground truth before PyCaret may rename it ──────────────
        has_ground_truth = (
            params.ground_truth_column is not None
            and params.ground_truth_column in data.columns
        )
        original_labels = data[params.ground_truth_column].copy() if has_ground_truth else None
        if params.ground_truth_column and not has_ground_truth:
            logging.warning(f"Column '{params.ground_truth_column}' not found – metrics skipped.")

        # ── Resolve model path ──────────────────────────────────────────────
        model_path = params.model_path.removesuffix(".pkl")
        model_dir = os.path.dirname(model_path)
        if not os.path.exists(model_path) and not os.path.exists(model_path + ".pkl"):
            if os.path.isdir(model_dir):
                base = os.path.basename(model_path)
                candidates = [f for f in os.listdir(model_dir) if f.startswith(base)]
                if candidates:
                    model_path = os.path.join(model_dir, candidates[0]).removesuffix(".pkl")
                    logging.info(f"Resolved model path to: {model_path}")

        # ── Load model ──────────────────────────────────────────────────────
        try:
            model = load_model(model_path)
            logging.info("Model loaded.")
        except FileNotFoundError as exc:
            return json.dumps({
                "status": "error",
                "error_message": f"Model file not found: {exc}",
                "model_path": params.model_path,
            }, indent=2)

        # ── Predict ─────────────────────────────────────────────────────────
        try:
            predictions = predict_model(model, data=data, raw_score=True)
        except Exception:
            predictions = predict_model(model, data=data)
        logging.info(f"Predictions generated – columns: {predictions.columns.tolist()}")

        predictions_path = os.path.join(output_dir, params.prediction_filename)
        predictions.to_csv(predictions_path, index=False)
        logging.info(f"Predictions saved → {predictions_path}")

        # ── Metrics ─────────────────────────────────────────────────────────
        metrics: dict = {}
        metrics_csv_path = comparison_path = confusion_matrix_path = metrics_json_path = None

        if has_ground_truth and original_labels is not None:
            y_true = original_labels

            # Resolve prediction column
            pred_col = next(
                (c for c in ["Label", "prediction_label", "predicted_y"] if c in predictions.columns),
                predictions.columns[-1],
            )
            y_pred = predictions[pred_col]
            logging.info(f"Prediction column: '{pred_col}'")

            metrics["accuracy"] = float(accuracy_score(y_true, y_pred))
            logging.info(f"Accuracy: {metrics['accuracy']:.4f}")

            comparison_df = pd.DataFrame({"actual": y_true, "predicted": y_pred})
            unique_classes = np.unique(y_true)
            is_binary = len(unique_classes) == 2

            # Confusion matrix
            cm = confusion_matrix(y_true, y_pred)
            cm_df = pd.DataFrame(cm)
            if hasattr(model, "classes_"):
                cm_df.index = cm_df.columns = model.classes_
            confusion_matrix_path = os.path.join(output_dir, "confusion_matrix.csv")
            cm_df.to_csv(confusion_matrix_path)

            if is_binary:
                # AUC
                prob_cols = [c for c in predictions.columns if c.startswith("Score_")]
                if not prob_cols:
                    prob_cols = [c for c in predictions.columns
                                 if "probability" in c.lower() or "score" in c.lower()]
                if prob_cols:
                    pos_class = sorted(unique_classes)[1]
                    prob_col = next(
                        (c for c in prob_cols if str(pos_class) in c or "1" in c),
                        prob_cols[0],
                    )
                    try:
                        y_prob = predictions[prob_col]
                        comparison_df["probability"] = y_prob
                        metrics["auc"] = float(roc_auc_score(y_true, y_prob))
                        logging.info(f"AUC: {metrics['auc']:.4f}")
                    except Exception as exc:
                        logging.warning(f"AUC calculation failed: {exc}")

                tn, fp, fn, tp = cm.ravel()
                metrics.update({
                    "precision":      float(precision_score(y_true, y_pred, zero_division=0)),
                    "recall":         float(recall_score(y_true, y_pred, zero_division=0)),
                    "sensitivity":    float(recall_score(y_true, y_pred, zero_division=0)),
                    "specificity":    float(tn / (tn + fp)) if (tn + fp) > 0 else 0.0,
                    "f1":             float(f1_score(y_true, y_pred, zero_division=0)),
                    "true_positives":  int(tp),
                    "false_positives": int(fp),
                    "true_negatives":  int(tn),
                    "false_negatives": int(fn),
                })
            else:
                metrics.update({
                    "precision_macro": float(precision_score(y_true, y_pred, average="macro", zero_division=0)),
                    "recall_macro":    float(recall_score(y_true, y_pred, average="macro", zero_division=0)),
                    "f1_macro":        float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
                })
                try:
                    prob_cols = [c for c in predictions.columns if c.startswith("Score_")]
                    if len(prob_cols) > 1:
                        from sklearn.preprocessing import label_binarize
                        classes = sorted(unique_classes)
                        y_true_bin = label_binarize(y_true, classes=classes)
                        metrics["auc_macro"] = float(
                            roc_auc_score(y_true_bin, predictions[prob_cols].values,
                                          multi_class="ovr", average="macro")
                        )
                except Exception as exc:
                    logging.warning(f"Multiclass AUC skipped: {exc}")

            comparison_path = os.path.join(output_dir, "prediction_vs_actual.csv")
            comparison_df.to_csv(comparison_path, index=False)

            metrics_csv_path = os.path.join(output_dir, "metrics_report.csv")
            pd.DataFrame([metrics]).to_csv(metrics_csv_path, index=False)

            metrics_json_path = os.path.join(output_dir, "metrics.json")
            with open(metrics_json_path, "w") as fh:
                json.dump(metrics, fh, indent=4)

            logging.info(f"Metrics: {metrics}")

        logging.info("Inference complete.")
        return json.dumps({
            "status": "success",
            "predictions_path": predictions_path,
            "num_predictions": len(predictions),
            "has_ground_truth": has_ground_truth,
            "metrics": metrics or None,
            "metrics_csv_path": metrics_csv_path,
            "metrics_json_path": metrics_json_path,
            "comparison_path": comparison_path,
            "confusion_matrix_path": confusion_matrix_path,
            "log_file": log_file,
            "input_path": params.input_path,
            "model_path": params.model_path,
            "output_dir": output_dir,
        }, indent=2)

    except Exception as exc:
        logging.error(f"Unhandled error in pycaret_run_inference: {exc}", exc_info=True)
        return json.dumps({
            "status": "error",
            "error_message": str(exc),
            "input_path": params.input_path,
            "model_path": params.model_path,
        }, indent=2)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # test classification training and inference
    pass
