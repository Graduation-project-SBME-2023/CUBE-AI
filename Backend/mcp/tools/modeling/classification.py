"""
PyCaret MCP Tools

MCP-compatible wrappers for PyCaret classification training and inference.
Follows the same structure as the multi-agent scratchpad MCP tools.

Tools exposed:
  - pycaret_train_classifier   : Full model comparison, tuning, blending, and evaluation
  - pycaret_run_inference      : Load a saved model and predict on new data

CHANGES:
  - pycaret_train_classifier now embeds chart_data in the JSON return value.
    chart_data contains:
      - comparison_table : list of {Model, Accuracy, AUC, F1, Precision, Recall, ...} rows
      - per_model        : {model_name: {cv_results, holdout_metrics, confusion_matrix,
                            roc_curve, pr_curve}}
      - fold             : int (number of CV folds used)
"""

import json
import logging
import os
import time
from typing import Optional
import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from .. import mcp
from ..helpers.workspace import WorkspacePath, base_on_workspace_dir
from tool_logging import ToolEvent, log_tool_state, TextPayload, ProgressPayload


# ── Helpers ────────────────────────────────────────────────────────────────────

def _setup_logging(log_file: str, verbose: bool = True) -> None:
    logger = logging.getLogger(__name__)
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    fh = logging.FileHandler(log_file, mode="a")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(logging.WARNING)

    # Configure only the module logger — root is never touched
    for h in logger.handlers[:]:
        logger.removeHandler(h)
    logger.addHandler(fh)
    logger.addHandler(ch)

    # PyCaret loggers sit in a different hierarchy so propagation won't
    # reach our module logger — attach the same file handler directly
    for name in ("pycaret", "pycaret.classification", "pycaret.internal"):
        pl = logging.getLogger(name)
        pl.setLevel(logging.DEBUG)
        pl.propagate = False  # don't leak into root either
        for h in pl.handlers[:]:
            pl.removeHandler(h)
        pl.addHandler(fh)     # same file handler instance


def _csv_list(value: Optional[str]) -> Optional[list]:
    if value:
        return [v.strip() for v in value.split(",") if v.strip()]
    return None


def _safe_float(v) -> Optional[float]:
    try:
        f = float(v)
        return None if np.isnan(f) or np.isinf(f) else round(f, 6)
    except Exception:
        return None


def _df_to_records(df: pd.DataFrame) -> list:
    """Convert a DataFrame to JSON-serialisable records, cleaning NaN/inf."""
    records = []
    for _, row in df.iterrows():
        rec = {}
        for k, v in row.items():
            if isinstance(v, float):
                rec[str(k)] = _safe_float(v)
            elif isinstance(v, (np.integer,)):
                rec[str(k)] = int(v)
            elif isinstance(v, (np.floating,)):
                rec[str(k)] = _safe_float(float(v))
            else:
                rec[str(k)] = v
        records.append(rec)
    return records


def _compute_roc(y_true, y_prob) -> Optional[dict]:
    """Return {fpr, tpr, thresholds} lists for an ROC curve, or None."""
    try:
        from sklearn.metrics import roc_curve
        fpr, tpr, thresh = roc_curve(y_true, y_prob)
        # Down-sample to 200 points max to keep payload small
        step = max(1, len(fpr) // 200)
        return {
            "fpr": [round(float(x), 5) for x in fpr[::step]],
            "tpr": [round(float(x), 5) for x in tpr[::step]],
        }
    except Exception:
        return None


def _compute_pr(y_true, y_prob) -> Optional[dict]:
    """Return {precision, recall} lists for a PR curve, or None."""
    try:
        from sklearn.metrics import precision_recall_curve
        prec, rec, _ = precision_recall_curve(y_true, y_prob)
        step = max(1, len(prec) // 200)
        return {
            "precision": [round(float(x), 5) for x in prec[::step]],
            "recall":    [round(float(x), 5) for x in rec[::step]],
        }
    except Exception:
        return None


def _holdout_metrics(predictions: pd.DataFrame, y_true: pd.Series) -> dict:
    """
    Compute accuracy, AUC, F1, precision, recall, specificity, and
    confusion-matrix values from a PyCaret predict_model() output.
    """
    from sklearn.metrics import (
        accuracy_score, f1_score, precision_score, recall_score,
        roc_auc_score, confusion_matrix,
    )

    pred_col = next(
        (c for c in ["Label", "prediction_label", "predicted_y"] if c in predictions.columns),
        predictions.columns[-1],
    )
    y_pred = predictions[pred_col]
    unique_classes = np.unique(y_true)
    is_binary = len(unique_classes) == 2

    # Macro averaging is defined even if a very small holdout set contains
    # only one observed class.
    metrics: dict = {
        "accuracy": _safe_float(accuracy_score(y_true, y_pred)),
        "f1":       _safe_float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "precision":_safe_float(precision_score(y_true, y_pred, average="macro", zero_division=0)),
        "recall":   _safe_float(recall_score(y_true, y_pred, average="macro", zero_division=0)),
    }

    cm = confusion_matrix(y_true, y_pred)
    metrics["confusion_matrix"] = cm.tolist()
    metrics["confusion_matrix_labels"] = sorted([str(c) for c in unique_classes])

    roc_data = pr_data = None
    if is_binary:
        tn, fp, fn, tp = cm.ravel()
        metrics["specificity"] = _safe_float(tn / (tn + fp)) if (tn + fp) > 0 else 0.0

        prob_cols = [c for c in predictions.columns if c.startswith("Score_")]
        if not prob_cols:
            prob_cols = [c for c in predictions.columns if "score" in c.lower() or "prob" in c.lower()]
        if prob_cols:
            pos_class = sorted(unique_classes)[1]
            prob_col = next(
                (c for c in prob_cols if str(pos_class) in c or "_1" in c),
                prob_cols[0],
            )
            y_prob = predictions[prob_col]
            try:
                metrics["auc"] = _safe_float(roc_auc_score(y_true, y_prob))
                roc_data = _compute_roc(y_true, y_prob)
                pr_data  = _compute_pr(y_true, y_prob)
            except Exception:
                pass

    return {"metrics": metrics, "roc_curve": roc_data, "pr_curve": pr_data}


# ── Input Models ───────────────────────────────────────────────────────────────

class TrainClassifierInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    input_path: WorkspacePath = Field(
        ..., description="Path to input CSV file with training data."
    )
    experiment_name: Optional[str] = Field(
        default=None, description="Experiment label. Auto-generated from timestamp if omitted."
    )
    fold: int = Field(
        default=5, description="Number of cross-validation folds."
    )
    session_id: Optional[int] = Field(
        default=None, description="Random seed for reproducibility. Randomly chosen if omitted."
    )
    use_gpu: bool = Field(default=False)
    fix_imbalance: bool = Field(default=True)
    data_split_stratify: bool = Field(default=True)
    data_split_shuffle: bool = Field(default=True)
    preprocess: bool = Field(default=True)
    ignore_features: Optional[str] = Field(default=None)
    numeric_features: Optional[str] = Field(default=None)
    categorical_features: Optional[str] = Field(default=None)
    date_features: Optional[str] = Field(default=None)
    n_select: int = Field(default=3)
    normalize: bool = Field(default=False)
    transformation: bool = Field(default=False)
    pca: bool = Field(default=False)
    pca_components: Optional[float] = Field(default=None)
    include_models: Optional[str] = Field(default=None)
    exclude_models: Optional[str] = Field(default=None)
    ignore_gpu_errors: bool = Field(default=True)


class RunInferenceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    input_path: WorkspacePath = Field(..., description="Path to CSV file containing rows to predict.")
    model_path: WorkspacePath = Field(..., description="Path to the saved PyCaret model.")
    ground_truth_column: Optional[str] = Field(default=None)
    prediction_filename: WorkspacePath = Field(default="predictions.csv")
    verbose: bool = Field(default=True)


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

    Returns:
        str: JSON with keys:
            - status, experiment_name, output_dir, final_model_path
            - individual_model_paths, model_test_predictions, model_summary
            - log_file, created_files
            - chart_data: {
                comparison_table : [{Model, Accuracy, AUC, F1, Precision, Recall, ...}]
                per_model        : {
                  model_name: {
                    cv_metrics     : {Accuracy, AUC, F1, ...}  (mean CV scores)
                    holdout_metrics: {accuracy, auc, f1, ...}
                    confusion_matrix        : [[int]]
                    confusion_matrix_labels : [str]
                    roc_curve  : {fpr:[...], tpr:[...]}  | null
                    pr_curve   : {precision:[...], recall:[...]} | null
                  }
                }
                fold: int
              }
    """
    try:
        import random

        output_dir = base_on_workspace_dir('modeling')
        os.makedirs(output_dir, exist_ok=True)

        target_column = "target"

        log_file = os.path.join(output_dir, "pycaret_classification.log")
        _setup_logging(log_file)
        logger = logging.getLogger(__name__)
        os.environ["TQDM_DISABLE"] = "1"

        experiment_name = params.experiment_name or f"classification_exp_{time.strftime('%Y%m%d_%H%M%S')}"
        session_id = params.session_id if params.session_id is not None else random.randint(1, 10_000)

        logger.info(f"Experiment: {experiment_name} | input: {params.input_path} | target: {target_column}")

        try:
            from pycaret.classification import (
                blend_models, compare_models, interpret_model,
                plot_model, predict_model, pull, save_model, setup, tune_model,
            )
            import pycaret
            logger.info(f"PyCaret {getattr(pycaret, '__version__', 'unknown')} imported")
        except ImportError as exc:
            raise ImportError("PyCaret is required. Install with: pip install pycaret") from exc

        # ── Stages: setup, compare, N×(tune+eval+plots), blend, finalise ──
        # Total = 3 fixed stages + 1 per model + 1 if blending
        n_models = params.n_select
        TOTAL_STAGES = 3 + n_models + 1  # setup | compare | tune×N | blend+finalise

        payload = TextPayload(message=f"Starting classification experiment '{experiment_name}'...")
        log_tool_state(ToolEvent("tool_started", "text", payload))

        # ── Stage 1: Load data & PyCaret setup ─────────────────────────────
        payload = ProgressPayload(current=1, total=TOTAL_STAGES, label="Loading data & configuring PyCaret")
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        data = pd.read_csv(params.input_path)
        logger.info(f"Data loaded: {data.shape[0]} rows × {data.shape[1]} cols")

        MIN_ROWS = 6
        if data.shape[0] < MIN_ROWS:
            return json.dumps({
                "status": "error",
                "error_message": (
                    f"Dataset has only {data.shape[0]} rows which is too few for reliable "
                    f"cross-validation training. At least {MIN_ROWS} rows are required. "
                    f"Consider collecting more data or merging with other datasets."
                ),
                "input_path": params.input_path,
                "rows": data.shape[0],
                "cols": data.shape[1],
            }, indent=2)

        if target_column not in data.columns:
            raise ValueError(f"Target column '{target_column}' not found in data.")

        if data[target_column].isna().any():
            return json.dumps({
                "status": "error",
                "error_message": "The target column contains missing values. Every training row needs a target label.",
                "input_path": params.input_path,
            }, indent=2)

        class_counts = data[target_column].value_counts()
        if len(class_counts) < 2:
            return json.dumps({
                "status": "error",
                "error_message": "Classification requires at least two target classes.",
                "input_path": params.input_path,
                "class_distribution": class_counts.to_dict(),
            }, indent=2)

        min_class_count = int(class_counts.min())
        n_rows = data.shape[0]
        requested_fold = max(2, params.fold)
        effective_fold = min(requested_fold, min_class_count)
        if effective_fold < 2:
            return json.dumps({
                "status": "error",
                "error_message": (
                    f"The rarest class has only {min_class_count} sample(s). At least two "
                    "samples per class are required for 2-fold cross-validation."
                ),
                "input_path": params.input_path,
                "class_distribution": class_counts.to_dict(),
                "fold": requested_fold,
            }, indent=2)

        # ── Adaptive dataset-size handling ───────────────────────────────
        # For small datasets we must:
        #   1. Cap CV folds so each fold has ≥ 2 samples per class
        #   2. Disable SMOTE when the minority class is too small for its
        #      default k_neighbors (5, meaning 6 total samples needed)
        #   3. Restrict candidate models to lightweight ones
        #   4. Use a larger train split to maximise training samples
        SMOTE_MIN_SAMPLES = 7  # SMOTE default k_neighbors=5 → need ≥ 6 minority samples in train
        SMALL_COHORT_THRESHOLD = 30

        # Estimate minority-class training samples after train/test split
        train_ratio = 0.8 if n_rows < 50 else 0.7
        estimated_train_minority = int(min_class_count * train_ratio)

        is_small_cohort = n_rows < SMALL_COHORT_THRESHOLD

        # Decide whether SMOTE is safe
        if params.fix_imbalance and estimated_train_minority < SMOTE_MIN_SAMPLES:
            effective_fix_imbalance = False
            logger.warning(
                "Disabling fix_imbalance (SMOTE): minority class has ~%d training "
                "samples, but SMOTE needs at least %d. ",
                estimated_train_minority, SMOTE_MIN_SAMPLES,
            )
        else:
            effective_fix_imbalance = params.fix_imbalance

        if is_small_cohort:
            effective_n_select = min(params.n_select, 2)
            logger.warning(
                "Small cohort (%d rows): using %d-fold CV, %d candidate model(s), "
                "fix_imbalance=%s.",
                n_rows, effective_fold, effective_n_select, effective_fix_imbalance,
            )
        else:
            effective_n_select = params.n_select

        # Store ground truth for holdout evaluation
        y_holdout = None  # will be set after setup splits data

        use_gpu = params.use_gpu
        if use_gpu:
            try:
                import cuml  # noqa: F401
            except ImportError:
                if params.ignore_gpu_errors:
                    logger.warning("cuml not installed – falling back to CPU.")
                    use_gpu = False
                else:
                    raise ImportError("GPU requested but cuml is not installed.")

        setup_kwargs: dict = dict(
            data=data,
            target=target_column,
            session_id=session_id,
            experiment_name=experiment_name,
            fold=effective_fold,
            use_gpu=use_gpu,
            fix_imbalance=effective_fix_imbalance,
            preprocess=params.preprocess,
            data_split_stratify=params.data_split_stratify,
            data_split_shuffle=params.data_split_shuffle,
            log_experiment=False,
            normalize=params.normalize,
            transformation=params.transformation,
            pca=params.pca,
        )
        # For small datasets, allocate more rows to training
        if n_rows < 50:
            setup_kwargs["train_size"] = 0.8
            logger.info("Using train_size=0.8 for small dataset (%d rows).", n_rows)
        for key, value in [
            ("ignore_features", _csv_list(params.ignore_features)),
            ("numeric_features", _csv_list(params.numeric_features)),
            ("categorical_features", _csv_list(params.categorical_features)),
            ("date_features", _csv_list(params.date_features)),
            ("pca_components", params.pca_components),
        ]:
            if value is not None:
                setup_kwargs[key] = value

        def _try_setup(kwargs):
            # PyCaret minor versions expose slightly different setup
            # parameters. Remove every unsupported optional parameter, not
            # just the first one, before falling back to the legacy path.
            kwargs = dict(kwargs)
            while True:
                try:
                    return setup(**kwargs)
                except TypeError as exc:
                    message = str(exc)
                    if "unexpected keyword argument" not in message:
                        break
                    parameter = message.split("'")[1]
                    if parameter not in kwargs:
                        break
                    logger.warning("Removing unsupported setup parameter '%s' and retrying.", parameter)
                    kwargs.pop(parameter)
            try:
                return setup(**kwargs)
            except TypeError as exc:
                bad = str(exc)
                if "unexpected keyword argument" in bad:
                    param = bad.split("'")[1]
                    logger.warning(f"Removing unsupported setup param '{param}' and retrying.")
                    kwargs.pop(param, None)
                    return setup(**kwargs)
                raise
            except Exception:
                minimal = {
                    "data": data,
                    "target": target_column,
                    "session_id": session_id,
                    "fold": effective_fold,
                    "fix_imbalance": effective_fix_imbalance,
                }
                if n_rows < 50:
                    minimal["train_size"] = 0.8
                logger.warning("Setup failed - retrying with minimal params.")
                return setup(**minimal)

        _try_setup(setup_kwargs)
        logger.info("PyCaret setup complete.")

        # Extract holdout (test) set ground truth from PyCaret's internal split
        try:
            from pycaret.classification import get_config
            X_test = get_config("X_test")
            y_holdout = get_config("y_test")
        except Exception:
            pass

        # ── Stage 2: Compare models ─────────────────────────────────────────
        payload = ProgressPayload(current=2, total=TOTAL_STAGES, label="Comparing models")
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        compare_kwargs: dict = {"n_select": effective_n_select}
        if is_small_cohort and not params.include_models:
            # These models can train with only a few observations per CV fold.
            compare_kwargs["include"] = ["lr", "nb", "dt", "ridge", "svm", "knn", "rf"]
        if params.include_models:
            compare_kwargs["include"] = _csv_list(params.include_models)
        if params.exclude_models:
            compare_kwargs["exclude"] = _csv_list(params.exclude_models)

        try:
            best = compare_models(**compare_kwargs)
        except Exception:
            logger.warning("compare_models failed with custom kwargs – retrying with defaults.")
            best = compare_models()

        if not isinstance(best, list):
            best = [best]
        logger.info(f"{len(best)} model(s) selected.")

        compare_df = pull()
        logger.debug(f"compare_models result table shape: {compare_df.shape}")
        logger.debug(f"compare_models result table:\n{compare_df.to_string()}")

        if not best:
            logger.error(f"best is empty. n_select={params.n_select}, "
                        f"models in results: {len(compare_df)}")
            raise Exception("No models trained successfully, dataset size is likely too small.")

        created_files: list = []

        compare_df = pull()
        compare_path = os.path.join(output_dir, "model_comparison_results.csv")
        compare_df.to_csv(compare_path, index=True)
        created_files.append(("Model Comparison Results", compare_path))

        # Build comparison table for chart_data
        comparison_table = _df_to_records(compare_df.reset_index().rename(columns={"index": "Model"}))

        def _get_model_label(model) -> str:
            """Return a short human-readable name for a PyCaret model object."""
            try:
                return type(model).__name__
            except Exception:
                return str(model)

        def _create_plots(model, plots_dir: str) -> list:
            plot_types = [
                "auc", "confusion_matrix", "class_report", "feature",
                "pr", "error", "learning", "calibration",
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
                        logger.warning(f"Plot '{pt}' skipped: {exc}")
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
                        logger.warning(f"Interpretation '{plot_type}' skipped: {exc}")
            finally:
                os.chdir(original_dir)
            return results

        def _eval_on_holdout(model, output_path: str, name: str):
            """Run holdout evaluation; return (csv_path | None, chart_dict | None)."""
            try:
                preds = predict_model(model)
                preds.to_csv(output_path, index=False)
                logger.info(f"Holdout predictions for '{name}' → {output_path}")

                chart_info = None
                if y_holdout is not None:
                    chart_info = _holdout_metrics(preds, y_holdout)

                return output_path, chart_info
            except Exception as exc:
                logger.error(f"Holdout eval failed for '{name}': {exc}")
                return None, None

        models_dir = os.path.join(output_dir, "models")
        os.makedirs(models_dir, exist_ok=True)
        tuned_models, individual_model_paths, model_test_predictions = [], {}, {}

        # chart_data accumulator
        per_model_chart: dict = {}

        # ── Stages 3…(2+N): Tune, evaluate & plot each model ───────────────
        for i, model in enumerate(best):
            name = f"tuned_model_{i + 1}"
            label = _get_model_label(model)
            model_dir = os.path.join(models_dir, name)
            os.makedirs(model_dir, exist_ok=True)
            logger.info(f"Tuning {name} ({i + 1}/{len(best)})")

            payload = ProgressPayload(
                current=3 + i,
                total=TOTAL_STAGES,
                label=f"Tuning & evaluating {label} ({i + 1}/{len(best)})",
            )
            log_tool_state(ToolEvent("tool_progress", "progress", payload))

            try:
                tuned = tune_model(model)
                tuned_models.append(tuned)

                cv_df = pull()
                cv_df.to_csv(os.path.join(model_dir, f"{name}_results.csv"), index=True)
                created_files.append((f"{name} Results", os.path.join(model_dir, f"{name}_results.csv")))

                # Extract mean CV metrics for chart_data
                cv_metrics = {}
                if "Mean" in cv_df.index:
                    cv_metrics = {str(col): _safe_float(cv_df.loc["Mean", col])
                                  for col in cv_df.columns if col != "TT (Sec)"}
                else:
                    # Last row is usually the mean
                    last = cv_df.iloc[-1]
                    cv_metrics = {str(k): _safe_float(v) for k, v in last.items() if k != "TT (Sec)"}

                model_pkl = os.path.join(model_dir, name)
                try:
                    save_model(tuned, model_pkl)
                    individual_model_paths[name] = model_pkl
                    created_files.append((f"{name} Saved Model", model_pkl))
                except Exception as exc:
                    logger.error(f"Could not save {name}: {exc}")

                plots_dir_path = os.path.join(model_dir, "plots")
                os.makedirs(plots_dir_path, exist_ok=True)
                created_files.extend(_create_plots(tuned, plots_dir_path))
                created_files.extend(_create_interpretations(tuned, plots_dir_path))

                holdout_path = os.path.join(model_dir, f"holdout_predictions_{name}.csv")
                result_path, chart_info = _eval_on_holdout(tuned, holdout_path, name)

                if result_path:
                    model_test_predictions[name] = result_path
                    created_files.append((f"{name} Holdout Predictions", result_path))

                per_model_chart[name] = {
                    "label": label,
                    "cv_metrics": cv_metrics,
                    "holdout_metrics": chart_info.get("metrics", {})           if chart_info else {},
                    # _holdout_metrics keeps confusion data inside its metrics
                    # dictionary. Reading it at the outer level always yielded
                    # null, even when a confusion-matrix image was generated.
                    "confusion_matrix": chart_info.get("metrics", {}).get("confusion_matrix") if chart_info else None,
                    "confusion_matrix_labels": chart_info.get("metrics", {}).get("confusion_matrix_labels") if chart_info else None,
                    "roc_curve": chart_info.get("roc_curve")                   if chart_info else None,
                    "pr_curve":  chart_info.get("pr_curve")                    if chart_info else None,
                }

            except Exception as exc:
                logger.error(f"Failed to process {name}: {exc}")

        # ── Final stage: Blend (if applicable) + save final model ──────────
        payload = ProgressPayload(
            current=TOTAL_STAGES,
            total=TOTAL_STAGES,
            label="Blending models & saving final model" if len(tuned_models) > 1 else "Saving final model",
        )
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        blended_model = None
        if len(tuned_models) > 1:
            blend_dir = os.path.join(models_dir, "blended_model")
            os.makedirs(blend_dir, exist_ok=True)
            try:
                blended_model = blend_models(tuned_models)
                blend_cv = pull()
                blend_cv.to_csv(os.path.join(blend_dir, "blended_model_results.csv"), index=True)
                created_files.append(("Blended Model Results", os.path.join(blend_dir, "blended_model_results.csv")))

                blend_cv_metrics = {}
                if "Mean" in blend_cv.index:
                    blend_cv_metrics = {str(col): _safe_float(blend_cv.loc["Mean", col])
                                        for col in blend_cv.columns if col != "TT (Sec)"}

                blend_pkl = os.path.join(blend_dir, "blended_model")
                try:
                    save_model(blended_model, blend_pkl)
                    individual_model_paths["blended_model"] = blend_pkl
                    created_files.append(("Blended Model Saved Model", blend_pkl))
                except Exception as exc:
                    logger.error(f"Could not save blended model: {exc}")

                blend_plots_dir = os.path.join(blend_dir, "plots")
                os.makedirs(blend_plots_dir, exist_ok=True)
                created_files.extend(_create_plots(blended_model, blend_plots_dir))
                created_files.extend(_create_interpretations(blended_model, blend_plots_dir))

                holdout_path = os.path.join(blend_dir, "holdout_predictions_blended_model.csv")
                result_path, chart_info = _eval_on_holdout(blended_model, holdout_path, "blended_model")
                if result_path:
                    model_test_predictions["blended_model"] = result_path
                    created_files.append(("Blended Model Holdout Predictions", result_path))

                per_model_chart["blended_model"] = {
                    "label": "Blended",
                    "cv_metrics": blend_cv_metrics,
                    "holdout_metrics": chart_info.get("metrics", {})           if chart_info else {},
                    "confusion_matrix": chart_info.get("metrics", {}).get("confusion_matrix") if chart_info else None,
                    "confusion_matrix_labels": chart_info.get("metrics", {}).get("confusion_matrix_labels") if chart_info else None,
                    "roc_curve": chart_info.get("roc_curve")                   if chart_info else None,
                    "pr_curve":  chart_info.get("pr_curve")                    if chart_info else None,
                }

            except Exception as exc:
                logger.error(f"Blending failed: {exc}")

        final_model = blended_model or (tuned_models[0] if tuned_models else best[0])

        final_model_path = os.path.join(output_dir, f"{experiment_name}_final_model")
        try:
            save_model(final_model, final_model_path)
            created_files.append(("Final Model", final_model_path))
        except Exception as exc:
            logger.error(f"Could not save final model: {exc}")
            final_model_path = None

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

        logger.info("Classification experiment complete.")

        payload = TextPayload(message="Classification Experiment...Done")
        log_tool_state(ToolEvent("tool_finished", "text", payload))

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
            # ── NEW: chart data for frontend visualizations ─────────────────
            "chart_data": {
                "comparison_table": comparison_table,
                "per_model": per_model_chart,
                "fold": effective_fold,
            },
        }, indent=2)

    except Exception as exc:
        logger.error(f"Unhandled error in pycaret_train_classifier: {exc}", exc_info=True)
        return json.dumps({
            "status": "error",
            "error_message": str(exc),
            "input_path": params.input_path,
            "output_dir": output_dir,
        }, indent=2)


# ── Inference tool is unchanged ────────────────────────────────────────────────

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
    """Load a saved PyCaret model and generate predictions on new data."""
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
        logger = logging.getLogger(__name__)

        logger.info(f"Inference | model: {params.model_path} | input: {params.input_path}")

        try:
            from pycaret.classification import load_model, predict_model
        except ImportError as exc:
            raise ImportError("PyCaret is required. Install with: pip install pycaret") from exc

        data = pd.read_csv(params.input_path)
        logger.info(f"Data loaded: {data.shape}")

        has_ground_truth = (
            params.ground_truth_column is not None
            and params.ground_truth_column in data.columns
        )
        original_labels = data[params.ground_truth_column].copy() if has_ground_truth else None
        if params.ground_truth_column and not has_ground_truth:
            logger.warning(f"Column '{params.ground_truth_column}' not found – metrics skipped.")

        model_path = params.model_path.removesuffix(".pkl")
        model_dir = os.path.dirname(model_path)
        if not os.path.exists(model_path) and not os.path.exists(model_path + ".pkl"):
            if os.path.isdir(model_dir):
                base = os.path.basename(model_path)
                candidates = [f for f in os.listdir(model_dir) if f.startswith(base)]
                if candidates:
                    model_path = os.path.join(model_dir, candidates[0]).removesuffix(".pkl")

        try:
            model = load_model(model_path)
        except FileNotFoundError as exc:
            return json.dumps({"status": "error", "error_message": f"Model file not found: {exc}"}, indent=2)

        try:
            predictions = predict_model(model, data=data, raw_score=True)
        except Exception:
            predictions = predict_model(model, data=data)

        predictions_path = os.path.join(output_dir, params.prediction_filename)
        predictions.to_csv(predictions_path, index=False)

        metrics: dict = {}
        metrics_csv_path = comparison_path = confusion_matrix_path = metrics_json_path = None

        if has_ground_truth and original_labels is not None:
            y_true = original_labels
            pred_col = next(
                (c for c in ["Label", "prediction_label", "predicted_y"] if c in predictions.columns),
                predictions.columns[-1],
            )
            y_pred = predictions[pred_col]
            metrics["accuracy"] = float(accuracy_score(y_true, y_pred))
            unique_classes = np.unique(y_true)
            is_binary = len(unique_classes) == 2
            cm = confusion_matrix(y_true, y_pred)
            cm_df = pd.DataFrame(cm)
            if hasattr(model, "classes_"):
                cm_df.index = cm_df.columns = model.classes_
            confusion_matrix_path = os.path.join(output_dir, "confusion_matrix.csv")
            cm_df.to_csv(confusion_matrix_path)

            if is_binary:
                prob_cols = [c for c in predictions.columns if c.startswith("Score_")]
                if not prob_cols:
                    prob_cols = [c for c in predictions.columns if "probability" in c.lower() or "score" in c.lower()]
                if prob_cols:
                    pos_class = sorted(unique_classes)[1]
                    prob_col = next((c for c in prob_cols if str(pos_class) in c or "1" in c), prob_cols[0])
                    try:
                        y_prob = predictions[prob_col]
                        metrics["auc"] = float(roc_auc_score(y_true, y_prob))
                    except Exception:
                        pass
                tn, fp, fn, tp = cm.ravel()
                metrics.update({
                    "precision": float(precision_score(y_true, y_pred, zero_division=0)),
                    "recall": float(recall_score(y_true, y_pred, zero_division=0)),
                    "sensitivity": float(recall_score(y_true, y_pred, zero_division=0)),
                    "specificity": float(tn / (tn + fp)) if (tn + fp) > 0 else 0.0,
                    "f1": float(f1_score(y_true, y_pred, zero_division=0)),
                    "true_positives": int(tp),
                    "false_positives": int(fp),
                    "true_negatives": int(tn),
                    "false_negatives": int(fn),
                })
            else:
                metrics.update({
                    "precision_macro": float(precision_score(y_true, y_pred, average="macro", zero_division=0)),
                    "recall_macro": float(recall_score(y_true, y_pred, average="macro", zero_division=0)),
                    "f1_macro": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
                })

            comparison_df = pd.DataFrame({"actual": y_true, "predicted": y_pred})
            comparison_path = os.path.join(output_dir, "prediction_vs_actual.csv")
            comparison_df.to_csv(comparison_path, index=False)
            metrics_csv_path = os.path.join(output_dir, "metrics_report.csv")
            pd.DataFrame([metrics]).to_csv(metrics_csv_path, index=False)
            metrics_json_path = os.path.join(output_dir, "metrics.json")
            with open(metrics_json_path, "w") as fh:
                json.dump(metrics, fh, indent=4)

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
        logger.error(f"Unhandled error in pycaret_run_inference: {exc}", exc_info=True)
        return json.dumps({
            "status": "error",
            "error_message": str(exc),
            "input_path": params.input_path,
            "model_path": params.model_path,
        }, indent=2)


if __name__ == "__main__":
    pass
