"""
PyCaret Regression MCP Tools

MCP-compatible wrappers for PyCaret regression training and inference.
Follows the same structure as the multi-agent scratchpad and classification MCP tools.

Tools exposed:
  - pycaret_train_regressor    : Full model comparison, tuning, blending, and evaluation
  - pycaret_run_regression_inference : Load a saved model and predict on new data
"""

import json
import logging
import os
import time
from typing import Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from .. import mcp  # same import pattern as scratchpad
from ..helpers.target_columns import load_feature_schema, resolve_target_column
from ..helpers.workspace import WorkspacePath, base_on_workspace_dir

# ── Constants ──────────────────────────────────────────────────────────────────

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "../../../agent_system/workspace")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _setup_logging(log_file: str, verbose: bool = True) -> None:
    """Configure root logger to write to file and stdout."""
    logger = logging.getLogger()
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


def _safe_float(value) -> Optional[float]:
    try:
        converted = float(value)
        return round(converted, 6) if np.isfinite(converted) else None
    except (TypeError, ValueError):
        return None


def _df_to_records(frame: pd.DataFrame) -> list[dict]:
    records = []
    for _, row in frame.iterrows():
        records.append({
            str(column): (
                int(value) if isinstance(value, np.integer)
                else _safe_float(value) if isinstance(value, (float, np.floating))
                else value
            )
            for column, value in row.items()
        })
    return records


def _regression_holdout_metrics(predictions: pd.DataFrame, y_true: pd.Series) -> dict:
    from sklearn.metrics import (
        explained_variance_score,
        mean_absolute_error,
        mean_squared_error,
        median_absolute_error,
        r2_score,
    )

    prediction_column = next(
        (
            column for column in ("prediction_label", "Label", "predicted_y", "Prediction")
            if column in predictions.columns
        ),
        predictions.columns[-1],
    )
    actual = np.asarray(y_true, dtype=float)
    predicted = np.asarray(predictions[prediction_column], dtype=float)
    if len(actual) != len(predicted):
        raise ValueError(
            f"Holdout target/prediction length mismatch: {len(actual)} != {len(predicted)}"
        )
    mse = mean_squared_error(actual, predicted)
    has_variation = (
        len(actual) > 1
        and not np.isclose(np.std(actual), 0.0)
        and not np.isclose(np.std(predicted), 0.0)
    )
    pearson = np.corrcoef(actual, predicted)[0, 1] if has_variation else np.nan
    metrics = {
        "MAE": _safe_float(mean_absolute_error(actual, predicted)),
        "MSE": _safe_float(mse),
        "RMSE": _safe_float(np.sqrt(mse)),
        "R2": _safe_float(r2_score(actual, predicted)),
        "MedianAE": _safe_float(median_absolute_error(actual, predicted)),
        "ExplainedVariance": _safe_float(explained_variance_score(actual, predicted)),
        "Pearson": _safe_float(pearson),
    }
    indices = np.arange(len(actual))
    if len(indices) > 500:
        indices = np.linspace(0, len(indices) - 1, 500, dtype=int)
    points = [
        {
            "actual": round(float(actual[index]), 6),
            "predicted": round(float(predicted[index]), 6),
            "residual": round(float(actual[index] - predicted[index]), 6),
        }
        for index in indices
    ]
    return {"metrics": metrics, "prediction_points": points}


# ── Input Models ───────────────────────────────────────────────────────────────

class TrainRegressorInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    input_path: WorkspacePath = Field(
        ..., description="Path to input CSV file with training data."
    )
    target_column: Optional[str] = Field(
        default=None,
        description=(
            "Continuous outcome column. Omit to resolve target/y/label/outcome or "
            "feature_schema.json automatically."
        ),
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
    use_gpu: bool = Field(
        default=False, description="Enable GPU acceleration (requires cuml)."
    )
    data_split_stratify: bool = Field(
        default=False,
        description="Use stratified train/test splitting. Usually False for regression.",
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
        default=8, description="Number of top models to select for tuning and blending."
    )
    normalize: bool = Field(
        default=True,
        description="Normalize numeric features before training. Usually True for regression.",
    )
    transformation: bool = Field(
        default=True,
        description="Apply power transformation to numeric features. Usually True for regression.",
    )
    pca: bool = Field(
        default=False, description="Reduce dimensionality with PCA."
    )
    pca_components: Optional[float] = Field(
        default=None,
        description="PCA components: float 0–1 for variance ratio, int > 1 for exact count.",
    )
    include_models: Optional[str] = Field(
        default="lr,ridge,lasso,knn,svm,dt,rf,gbr",
        description=(
            "Comma-separated PyCaret model IDs. Defaults to OLS, Ridge, Lasso, KNN, "
            "SVR, Decision Tree, Random Forest, and Gradient Boosting."
        ),
    )
    exclude_models: Optional[str] = Field(
        default=None, description="Comma-separated PyCaret model IDs to exclude from comparison."
    )
    ignore_gpu_errors: bool = Field(
        default=True, description="Fall back to CPU silently when GPU/cuml is unavailable."
    )


class RunRegressionInferenceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    input_path: str = Field(
        ..., description="Path to CSV file containing rows to predict."
    )
    model_path: str = Field(
        ..., description="Path to the saved PyCaret model (with or without .pkl extension)."
    )
    # output_dir: Optional[str] = Field(
    #     default=None,
    #     description="Directory to write prediction outputs. Defaults to the input file's directory.",
    # )
    ground_truth_column: Optional[str] = Field(
        default=None,
        description=(
            "Column name holding ground-truth values. "
            "When supplied, MSE, RMSE, MAE, R², MAPE, explained variance, "
            "residual statistics, and a residual analysis table are calculated."
        ),
    )
    prediction_filename: str = Field(
        default="predictions.csv",
        description="File name for the saved predictions CSV.",
    )
    verbose: bool = Field(
        default=True, description="Emit detailed log messages."
    )


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="pycaret_train_regressor",
    annotations={
        "title": "Train a PyCaret Regression Pipeline",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def pycaret_train_regressor(params: TrainRegressorInput) -> str:
    """Compare, tune, and blend regression models with PyCaret.

    Workflow:
      1. Load CSV data and call pycaret.regression.setup().
      2. Compare all (or a filtered set of) models and select the top n_select.
      3. Tune each selected model individually.
      4. Blend the tuned models (if more than one).
      5. For every model (tuned + blended) generate regression plots and SHAP
         interpretations, and produce holdout-set predictions.
      6. Save all artefacts under output_dir and return a structured result object.

    Args:
        params (TrainRegressorInput): Full training configuration – see field descriptions.

    Returns:
        str: JSON with keys:
            - status                 : "success" or "error"
            - experiment_name        : str
            - output_dir             : str
            - final_model_path       : path to the final saved model
            - individual_model_paths : {model_name: path}
            - model_test_predictions : {model_name: predictions_csv_path}
            - model_summary          : path to summary CSV
            - log_file               : path to the run log
            - created_files          : [(label, path), ...]
            - error_message          : present only when status == "error"
    """
    try:
        import random

        output_dir = base_on_workspace_dir('modeling') or os.path.join(WORKSPACE_DIR, 'modeling')

        os.makedirs(output_dir, exist_ok=True)
        log_file = os.path.join(output_dir, "pycaret_regression.log")
        _setup_logging(log_file)

        experiment_name = params.experiment_name or f"regression_exp_{time.strftime('%Y%m%d_%H%M%S')}"
        session_id = params.session_id if params.session_id is not None else random.randint(1, 10_000)

        # ── Import PyCaret ──────────────────────────────────────────────────
        try:
            from pycaret.regression import (
                blend_models, compare_models, interpret_model,
                get_config, plot_model, predict_model, pull, save_model, setup, tune_model,
            )
            import pycaret
            logging.info(f"PyCaret {getattr(pycaret, '__version__', 'unknown')} imported")
        except ImportError as exc:
            raise ImportError("PyCaret is required. Install with: pip install pycaret") from exc

        # ── Load data ───────────────────────────────────────────────────────
        data = pd.read_csv(params.input_path)
        logging.info(f"Data loaded: {data.shape[0]} rows × {data.shape[1]} cols")
        target_column, target_resolution = resolve_target_column(
            data.columns,
            requested=params.target_column,
            schema=load_feature_schema(params.input_path),
        )
        logging.info(
            "Experiment: %s | input: %s | target: %s (%s)",
            experiment_name, params.input_path, target_column, target_resolution,
        )
        if data[target_column].isna().any():
            raise ValueError("The regression target contains missing values.")
        numeric_target = pd.to_numeric(data[target_column], errors="coerce")
        if numeric_target.isna().any():
            raise ValueError(
                f"Regression target '{target_column}' must contain only numeric values."
            )
        data[target_column] = numeric_target
        if data.shape[0] < 4:
            raise ValueError("Regression requires at least four labeled rows.")
        estimated_train_rows = max(2, int(np.floor(data.shape[0] * 0.7)))
        effective_fold = min(max(2, params.fold), estimated_train_rows)

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
            fold=effective_fold,
            use_gpu=use_gpu,
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
                minimal = {
                    "data": data,
                    "target": target_column,
                    "session_id": session_id,
                    "fold": effective_fold,
                }
                logging.warning("Setup failed – retrying with minimal params.")
                return setup(**minimal)

        _try_setup(setup_kwargs)
        logging.info("PyCaret setup complete.")
        try:
            y_holdout = get_config("y_test")
        except Exception as exc:
            logging.warning("Could not read holdout targets: %s", exc)
            y_holdout = None

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
        comparison_table = _df_to_records(
            compare_df.reset_index().rename(columns={"index": "Model"})
        )
        compare_path = os.path.join(output_dir, "model_comparison_results.csv")
        compare_df.to_csv(compare_path, index=True)
        created_files.append(("Model Comparison Results", compare_path))

        # ── Inner helpers ───────────────────────────────────────────────────
        def _create_plots(model, plots_dir: str) -> list:
            plot_types = [
                "residuals", "error", "cooks", "learning",
                "vc", "feature", "feature_all", "parameter",
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
                for plot_type, filename in [
                    ("summary", "SHAP Summary.png"),
                    ("correlation", "SHAP Correlation.png"),
                ]:
                    try:
                        interpret_model(model, plot=plot_type, save=True)
                        fp = os.path.join(plots_dir, filename)
                        if os.path.exists(fp):
                            results.append((f"Interpretation: {plot_type}", fp))
                    except Exception as exc:
                        logging.warning(f"Interpretation '{plot_type}' skipped: {exc}")
            finally:
                os.chdir(original_dir)
            return results

        def _eval_on_holdout(model, output_path: str, name: str):
            try:
                preds = predict_model(model)
                preds.to_csv(output_path, index=False)
                logging.info(f"Holdout predictions for '{name}' → {output_path}")
                chart_info = (
                    _regression_holdout_metrics(preds, y_holdout)
                    if y_holdout is not None else None
                )
                return output_path, chart_info
            except Exception as exc:
                logging.error(f"Holdout eval failed for '{name}': {exc}")
                return None, None

        # ── Tune individual models ──────────────────────────────────────────
        models_dir = os.path.join(output_dir, "models")
        os.makedirs(models_dir, exist_ok=True)
        tuned_models, individual_model_paths, model_test_predictions = [], {}, {}
        per_model_chart: dict = {}

        for i, model in enumerate(best):
            name = f"tuned_model_{i + 1}"
            label = type(model).__name__
            model_dir = os.path.join(models_dir, name)
            os.makedirs(model_dir, exist_ok=True)
            logging.info(f"Tuning {name} ({i + 1}/{len(best)})")

            try:
                tuned = tune_model(model)
                tuned_models.append(tuned)

                cv_df = pull()
                cv_df.to_csv(os.path.join(model_dir, f"{name}_results.csv"), index=True)
                created_files.append((f"{name} Results", os.path.join(model_dir, f"{name}_results.csv")))
                mean_row = cv_df.loc["Mean"] if "Mean" in cv_df.index else cv_df.iloc[-1]
                cv_metrics = {
                    str(column): _safe_float(value)
                    for column, value in mean_row.items()
                    if column != "TT (Sec)"
                }

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
                result_path, chart_info = _eval_on_holdout(tuned, holdout_path, name)
                if result_path:
                    model_test_predictions[name] = result_path
                    created_files.append((f"{name} Holdout Predictions", result_path))
                per_model_chart[name] = {
                    "label": label,
                    "cv_metrics": cv_metrics,
                    "holdout_metrics": chart_info.get("metrics", {}) if chart_info else {},
                    "prediction_points": chart_info.get("prediction_points", []) if chart_info else [],
                    "confusion_matrix": None,
                    "confusion_matrix_labels": None,
                    "roc_curve": None,
                    "pr_curve": None,
                }

            except Exception as exc:
                logging.error(f"Failed to process {name}: {exc}")

        # ── Blend models ────────────────────────────────────────────────────
        blended_model = None
        if len(tuned_models) > 1:
            blend_dir = os.path.join(models_dir, "blended_model")
            os.makedirs(blend_dir, exist_ok=True)
            try:
                blended_model = blend_models(tuned_models)
                blend_cv = pull()
                blend_cv.to_csv(os.path.join(blend_dir, "blended_model_results.csv"), index=True)
                created_files.append(("Blended Model Results", os.path.join(blend_dir, "blended_model_results.csv")))
                blend_mean = blend_cv.loc["Mean"] if "Mean" in blend_cv.index else blend_cv.iloc[-1]
                blend_cv_metrics = {
                    str(column): _safe_float(value)
                    for column, value in blend_mean.items()
                    if column != "TT (Sec)"
                }

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
                result_path, chart_info = _eval_on_holdout(blended_model, holdout_path, "blended_model")
                if result_path:
                    model_test_predictions["blended_model"] = result_path
                    created_files.append(("Blended Model Holdout Predictions", result_path))
                per_model_chart["blended_model"] = {
                    "label": "Blended Regressor",
                    "cv_metrics": blend_cv_metrics,
                    "holdout_metrics": chart_info.get("metrics", {}) if chart_info else {},
                    "prediction_points": chart_info.get("prediction_points", []) if chart_info else [],
                    "confusion_matrix": None,
                    "confusion_matrix_labels": None,
                    "roc_curve": None,
                    "pr_curve": None,
                }

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
        for idx, _ in enumerate(tuned_models):
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

        logging.info("Regression experiment complete.")
        return json.dumps({
            "status": "success",
            "task_type": "regression",
            "experiment_name": experiment_name,
            "target_column": target_column,
            "target_resolution": target_resolution,
            "output_dir": output_dir,
            "final_model_path": final_model_path,
            "individual_model_paths": individual_model_paths,
            "model_test_predictions": model_test_predictions,
            "model_summary": summary_path,
            "log_file": log_file,
            "created_files": created_files,
            "chart_data": {
                "task_type": "regression",
                "comparison_table": comparison_table,
                "per_model": per_model_chart,
                "fold": effective_fold,
                "metric_definitions": {
                    "MAE": "Mean Absolute Error (lower is better)",
                    "MSE": "Mean Squared Error (lower is better)",
                    "RMSE": "Root Mean Squared Error (lower is better)",
                    "R2": "Coefficient of Determination (higher is better)",
                    "MedianAE": "Median Absolute Error (lower is better)",
                    "ExplainedVariance": "Explained Variance (higher is better)",
                    "Pearson": "Pearson Correlation (higher is better)",
                },
            },
        }, indent=2)

    except Exception as exc:
        logging.error(f"Unhandled error in pycaret_train_regressor: {exc}", exc_info=True)
        return json.dumps({
            "status": "error",
            "error_message": str(exc),
            "input_path": params.input_path,
            "output_dir": output_dir,
        }, indent=2)


@mcp.tool(
    name="pycaret_run_regression_inference",
    annotations={
        "title": "Run Inference with a Saved PyCaret Regression Model",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def pycaret_run_regression_inference(params: RunRegressionInferenceInput) -> str:
    """Load a saved PyCaret regression model and generate predictions on new data.

    Optionally computes a full suite of regression metrics (MSE, RMSE, MAE, R²,
    explained variance, MAPE, residual statistics) and saves a binned residual
    analysis table when a ground-truth column is provided.

    Args:
        params (RunRegressionInferenceInput): Inference configuration – see field descriptions.

    Returns:
        str: JSON with keys:
            - status                : "success" or "error"
            - predictions_path      : path to the predictions CSV
            - num_predictions       : number of rows predicted
            - has_ground_truth      : bool
            - metrics               : dict of computed metrics (if ground_truth_column given)
            - metrics_csv_path      : path to metrics CSV
            - metrics_json_path     : path to metrics JSON
            - comparison_path       : path to prediction-vs-actual CSV (with residual columns)
            - residuals_path        : path to binned residual analysis CSV
            - log_file              : path to the run log
            - error_message         : present only when status == "error"
    """
    try:
        from sklearn.metrics import (
            explained_variance_score,
            mean_absolute_error,
            mean_absolute_percentage_error,
            mean_squared_error,
            r2_score,
        )

        output_dir = os.path.join(WORKSPACE_DIR, 'modeling') or os.path.dirname(params.input_path) or "."
        os.makedirs(output_dir, exist_ok=True)
        log_file = os.path.join(output_dir, "pycaret_regression_inference.log")
        _setup_logging(log_file, verbose=params.verbose)

        logging.info(f"Inference | model: {params.model_path} | input: {params.input_path}")

        # ── Import PyCaret ──────────────────────────────────────────────────
        try:
            from pycaret.regression import load_model, predict_model
        except ImportError as exc:
            raise ImportError("PyCaret is required. Install with: pip install pycaret") from exc

        # ── Load data ───────────────────────────────────────────────────────
        data = pd.read_csv(params.input_path)
        logging.info(f"Data loaded: {data.shape}")

        # ── Preserve ground truth ───────────────────────────────────────────
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
            predictions = predict_model(model, data=data)
        except Exception as exc:
            logging.error(f"predict_model failed: {exc}")
            return json.dumps({
                "status": "error",
                "error_message": f"Failed to make predictions: {exc}",
            }, indent=2)

        logging.info(f"Predictions generated – columns: {predictions.columns.tolist()}")

        predictions_path = os.path.join(output_dir, params.prediction_filename)
        predictions.to_csv(predictions_path, index=False)
        logging.info(f"Predictions saved → {predictions_path}")

        # ── Metrics ─────────────────────────────────────────────────────────
        metrics: dict = {}
        metrics_csv_path = comparison_path = residuals_path = metrics_json_path = None

        if has_ground_truth and original_labels is not None:
            y_true = original_labels

            # Resolve prediction column
            pred_col = next(
                (c for c in ["Label", "prediction_label", "predicted_y", "Prediction"]
                 if c in predictions.columns),
                predictions.columns[-1],
            )
            y_pred = predictions[pred_col]
            logging.info(f"Prediction column: '{pred_col}'")

            # Build comparison DataFrame with residual columns
            comparison_df = pd.DataFrame({"actual": y_true, "predicted": y_pred})
            comparison_df["residual"] = y_true - y_pred
            comparison_df["abs_residual"] = comparison_df["residual"].abs()
            comparison_df["squared_residual"] = comparison_df["residual"] ** 2

            nonzero_mask = y_true != 0
            comparison_df["percent_error"] = np.nan
            if nonzero_mask.any():
                comparison_df.loc[nonzero_mask, "percent_error"] = (
                    comparison_df.loc[nonzero_mask, "abs_residual"]
                    / y_true[nonzero_mask].abs()
                ) * 100

            # Core regression metrics
            mse = float(mean_squared_error(y_true, y_pred))
            metrics.update({
                "mse":               mse,
                "rmse":              float(np.sqrt(mse)),
                "mae":               float(mean_absolute_error(y_true, y_pred)),
                "r2":                float(r2_score(y_true, y_pred)),
                "explained_variance": float(explained_variance_score(y_true, y_pred)),
            })

            # MAPE – only on non-zero actuals
            try:
                if nonzero_mask.any():
                    metrics["mape"] = float(
                        mean_absolute_percentage_error(
                            y_true[nonzero_mask], y_pred[nonzero_mask]
                        ) * 100
                    )
            except Exception as exc:
                logging.warning(f"MAPE skipped: {exc}")

            # Residual descriptive stats
            residuals = comparison_df["residual"]
            metrics.update({
                "mean_residual":   float(residuals.mean()),
                "median_residual": float(residuals.median()),
                "min_residual":    float(residuals.min()),
                "max_residual":    float(residuals.max()),
                "std_residual":    float(residuals.std()),
            })

            for k, v in metrics.items():
                logging.info(f"{k}: {v:.4f}")

            # Binned residual analysis
            residual_bins = pd.cut(residuals, bins=10)
            residual_analysis = (
                comparison_df.groupby(residual_bins)["residual"]
                .agg(["count", "mean", "min", "max"])
                .reset_index()
            )
            residual_analysis["bin"] = residual_analysis["residual"].astype(str)
            residual_analysis = residual_analysis.drop(columns="residual")

            residuals_path = os.path.join(output_dir, "residual_analysis.csv")
            residual_analysis.to_csv(residuals_path, index=False)

            comparison_path = os.path.join(output_dir, "prediction_vs_actual.csv")
            comparison_df.to_csv(comparison_path, index=False)

            metrics_csv_path = os.path.join(output_dir, "metrics_report.csv")
            pd.DataFrame([metrics]).to_csv(metrics_csv_path, index=False)

            metrics_json_path = os.path.join(output_dir, "metrics.json")
            with open(metrics_json_path, "w") as fh:
                json.dump(metrics, fh, indent=4)

            logging.info(f"Metrics written → {metrics_csv_path}")

        logging.info("Regression inference complete.")
        return json.dumps({
            "status": "success",
            "predictions_path": predictions_path,
            "num_predictions": len(predictions),
            "has_ground_truth": has_ground_truth,
            "metrics": metrics or None,
            "metrics_csv_path": metrics_csv_path,
            "metrics_json_path": metrics_json_path,
            "comparison_path": comparison_path,
            "residuals_path": residuals_path,
            "log_file": log_file,
            "input_path": params.input_path,
            "model_path": params.model_path,
            "output_dir": output_dir,
        }, indent=2)

    except Exception as exc:
        logging.error(f"Unhandled error in pycaret_run_regression_inference: {exc}", exc_info=True)
        return json.dumps({
            "status": "error",
            "error_message": str(exc),
            "input_path": params.input_path,
            "model_path": params.model_path,
        }, indent=2)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
