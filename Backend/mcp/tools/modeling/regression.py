"""PyCaret Regression MCP Tools."""

import io
import json
import logging
import os
from typing import Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .. import mcp  # same import pattern as scratchpad
from ..feature_extraction.digests import bytes_digest
from ..helpers import domain_api
from ..helpers.workspace import WorkspacePath, get_workspace
from . import results
from .evaluation import cv_row as _cv_row, regression_metrics as _regression_metrics
from .holdout import resolve_selection_holdout
from storage_paths import HYPOTHESES_DIR, TEMP_DIR

# ── Constants ──────────────────────────────────────────────────────────────────

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", str(HYPOTHESES_DIR / "workspace_default"))


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


# ── Input Models ───────────────────────────────────────────────────────────────

class TrainRegressorInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    feature_set_id: Optional[str] = Field(
        default=None,
        description=(
            "The frozen feature set to train on — the normal case. Its label is joined "
            "from the cohort, and evaluation runs on exactly the rows feature selection "
            "withheld."
        ),
    )
    input_path: WorkspacePath = Field(
        default=None,
        description=(
            "A generic CSV to train on instead of a feature set. Its exact bytes are "
            "captured as an input artifact so the result stays reproducible."
        ),
    )
    holdout_path: Optional[str] = Field(
        default=None,
        description=(
            "With input_path only: a CSV of rows to evaluate on instead of letting "
            "PyCaret re-split. Auto-detected from a sibling '<input>_holdout.csv' when "
            "omitted. A feature set carries its own holdout."
        ),
    )
    experiment_name: Optional[str] = Field(
        default=None,
        description="Display label. The comparison itself is identified by its input and configuration.",
    )
    fold: int = Field(
        default=10, description="Number of cross-validation folds."
    )
    session_id: Optional[int] = Field(
        default=None,
        description=(
            "Random seed. Derived from the input's digest when omitted, so a replay "
            "trains with the same seed."
        ),
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
        default=3, description="Number of top models to select for tuning and blending."
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
        default=None, description="Comma-separated PyCaret model IDs to include in comparison."
    )
    exclude_models: Optional[str] = Field(
        default=None, description="Comma-separated PyCaret model IDs to exclude from comparison."
    )
    ignore_gpu_errors: bool = Field(
        default=True, description="Fall back to CPU silently when GPU/cuml is unavailable."
    )

    @model_validator(mode="after")
    def _one_source(self) -> "TrainRegressorInput":
        results.check_one_source(self.feature_set_id, self.input_path, self.holdout_path)
        return self


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
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def pycaret_train_regressor(params: TrainRegressorInput) -> str:
    """Compare, tune, and blend regression models with PyCaret."""
    output_dir = None
    try:
        run_id = domain_api.require_run()
        work_root = str(TEMP_DIR / "modeling" / run_id)
        target_column = 'target'

        prepared = await results.prepare_input(
            feature_set_id=params.feature_set_id,
            input_path=params.input_path,
            holdout_path=(
                resolve_selection_holdout(params.input_path, params.holdout_path)
                if params.input_path
                else None
            ),
            work_dir=os.path.join(work_root, "input", results.slug(params.feature_set_id or "csv")),
        )
        session_id = (
            params.session_id
            if params.session_id is not None
            else results.deterministic_seed(prepared.dataset_digest)
        )
        training_config = {
            "task": "regression",
            "source": prepared.feature_set_id or prepared.dataset_digest,
            "seed": session_id,
            **{
                name: getattr(params, name)
                for name in (
                    "fold", "use_gpu", "data_split_stratify", "data_split_shuffle",
                    "preprocess", "ignore_features", "numeric_features",
                    "categorical_features", "date_features", "n_select", "normalize",
                    "transformation", "pca", "pca_components", "include_models",
                    "exclude_models",
                )
            },
        }
        comparison = results.comparison_key("regression", prepared.source_label, training_config)
        experiment_name = params.experiment_name or comparison

        # ── Replay: a recorded comparison is read, not retrained ─────
        recorded = await domain_api.get_model_comparison(comparison)
        if recorded:
            return json.dumps(
                results.recorded_response(
                    recorded, experiment_name=experiment_name, prepared=prepared, reused=True
                ),
                indent=2,
            )

        output_dir = os.path.join(work_root, results.slug(comparison))
        os.makedirs(output_dir, exist_ok=True)
        log_file = os.path.join(output_dir, "pycaret_regression.log")
        _setup_logging(log_file)

        logging.info(f"Experiment: {experiment_name} | comparison: {comparison} | target: {target_column}")

        # ── Import PyCaret ──────────────────────────────────────────────────
        try:
            from pycaret.regression import (
                blend_models, compare_models, interpret_model,
                plot_model, predict_model, pull, save_model, setup, tune_model,
            )
            import pycaret
            logging.info(f"PyCaret {getattr(pycaret, '__version__', 'unknown')} imported")
        except ImportError as exc:
            raise ImportError("PyCaret is required. Install with: pip install pycaret") from exc

        # ── Load data ───────────────────────────────────────────────────────
        data = pd.read_csv(prepared.train_path)
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

        # Honour the withheld rows (a feature set's own holdout, or an explicit
        # holdout CSV): the evaluated rows never reached the feature selector.
        selection_holdout_used = False
        holdout_rows = None
        if prepared.holdout_path:
            try:
                holdout_df = pd.read_csv(prepared.holdout_path)
                if target_column in holdout_df.columns and not holdout_df.empty:
                    train_df = data
                    if len(train_df) > len(holdout_df):
                        merged = train_df.merge(
                            holdout_df.drop_duplicates(), how="left", indicator=True
                        )
                        if (merged["_merge"] == "both").any():
                            train_df = train_df[merged["_merge"].values == "left_only"]
                    if not train_df.empty:
                        train_df, holdout_df = results.disjoint_indices(train_df, holdout_df)
                        setup_kwargs["data"] = train_df
                        setup_kwargs["test_data"] = holdout_df
                        setup_kwargs.pop("train_size", None)
                        selection_holdout_used = True
                        holdout_rows = int(len(holdout_df))
                        logging.info(
                            "Using the withheld rows (%d) as the evaluation set; "
                            "PyCaret will not re-split.",
                            len(holdout_df),
                        )
            except Exception as exc:
                logging.warning(
                    "Could not use the withheld rows (%s); falling back to PyCaret's "
                    "own split, which reintroduces selection leakage.",
                    exc,
                )

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
                    "data": kwargs.get("data", data),
                    "target": target_column,
                    "session_id": session_id,
                }
                # Never silently drop the selection holdout on the retry path; losing
                # it here would reintroduce the leak with no signal in the output.
                if kwargs.get("test_data") is not None:
                    minimal["test_data"] = kwargs["test_data"]
                logging.warning("Setup failed – retrying with minimal params.")
                return setup(**minimal)

        _try_setup(dict(setup_kwargs))
        logging.info("PyCaret setup complete.")

        eval_kind = results.evaluation_kind(prepared, selection_holdout_used)
        generalisation = results.generalisation_estimate(prepared, selection_holdout_used)

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

        variants: list = []
        for model_id, row in pull().iterrows():
            variants.append({
                "variant_key": f"compare_{results.slug(model_id)}",
                "algorithm": str(row.get("Model", model_id)),
                "parameters": results.versioned({"pycaret_model_id": str(model_id)}),
                "metrics": results.versioned({
                    "stage": "comparison",
                    "fold": params.fold,
                    "cv": results.json_safe({
                        str(column): value
                        for column, value in row.items()
                        if column not in ("Model", "TT (Sec)")
                    }),
                }),
                "selected": False,
                "status": "ready",
            })

        # ── Inner helpers ───────────────────────────────────────────────────
        def _create_plots(model, plots_dir: str) -> None:
            plot_types = [
                "residuals", "error", "cooks", "learning",
                "vc", "feature", "feature_all", "parameter",
            ]
            original_dir = os.getcwd()
            try:
                os.chdir(plots_dir)
                for pt in plot_types:
                    try:
                        plot_model(model, plot=pt, save=True)
                    except Exception as exc:
                        logging.warning(f"Plot '{pt}' skipped: {exc}")
            finally:
                os.chdir(original_dir)

        def _create_interpretations(model, plots_dir: str) -> None:
            original_dir = os.getcwd()
            try:
                os.chdir(plots_dir)
                for plot_type in ("summary", "correlation"):
                    try:
                        interpret_model(model, plot=plot_type, save=True)
                    except Exception as exc:
                        logging.warning(f"Interpretation '{plot_type}' skipped: {exc}")
            finally:
                os.chdir(original_dir)

        figures: list = []
        warnings: list = []

        async def _record_variant(name, label, model, stage, cv_metrics, parameters=None):
            model_file = None
            base = results.model_file_base(get_workspace(), comparison, name)
            try:
                save_model(model, base)
                model_file = {
                    "name": results.model_file_logical_name(comparison, name),
                    "digest": results.file_digest(base + ".pkl"),
                }
            except Exception as exc:
                logging.error(f"Could not save {name}: {exc}")
                warnings.append(f"{name}: the fitted pipeline could not be saved ({exc})")

            plots_dir = os.path.join(output_dir, results.slug(name), "plots")
            os.makedirs(plots_dir, exist_ok=True)
            _create_plots(model, plots_dir)
            _create_interpretations(model, plots_dir)
            published, figure_warnings = await results.publish_figures(
                plots_dir, run_id=run_id, comparison=comparison, variant=name
            )
            figures.extend(published)
            warnings.extend(figure_warnings)

            predictions_artifact_id = None
            evaluation = None
            try:
                preds = predict_model(model)
            except Exception as exc:
                logging.error(f"Holdout eval failed for '{name}': {exc}")
                preds = None
            if preds is not None:
                artifact = await results.publish_predictions(
                    preds,
                    run_id=run_id,
                    comparison=comparison,
                    variant=name,
                    metadata={"variant_key": name, "evaluation": eval_kind},
                )
                predictions_artifact_id = str(artifact["artifact_id"])
                holdout_metrics = _regression_metrics(preds, target_column)
                if holdout_metrics is not None:
                    evaluation = {
                        "kind": eval_kind,
                        "n_cases": int(len(preds)),
                        "generalisation_estimate": generalisation,
                        "metrics": holdout_metrics,
                    }
            return {
                "variant_key": name,
                "algorithm": label,
                "parameters": results.versioned(parameters or results.estimator_spec(model)),
                "metrics": results.versioned({
                    "stage": stage,
                    "fold": params.fold,
                    "cv": cv_metrics,
                    "evaluation": evaluation,
                }),
                "predictions_artifact_id": predictions_artifact_id,
                "model_file_name": model_file["name"] if model_file else None,
                "model_file_digest": model_file["digest"] if model_file else None,
                "selected": False,
                "status": "ready",
            }

        # ── Tune individual models ──────────────────────────────────────────
        tuned_models: list = []
        for i, model in enumerate(best):
            name = f"tuned_model_{i + 1}"
            label = type(model).__name__
            logging.info(f"Tuning {name} ({i + 1}/{len(best)})")
            try:
                tuned = tune_model(model)
                variants.append(await _record_variant(name, label, tuned, "tuned", _cv_row(pull())))
                tuned_models.append((name, tuned))
            except Exception as exc:
                logging.error(f"Failed to process {name}: {exc}")
                variants.append({
                    "variant_key": name,
                    "algorithm": label,
                    "parameters": results.versioned(results.estimator_spec(model)),
                    "metrics": results.versioned({"stage": "tuned", "error": str(exc)[:500]}),
                    "selected": False,
                    "status": "failed",
                })

        # ── Blend models, then select the final one ─────────────────────────
        final_name = None
        if len(tuned_models) > 1:
            try:
                blended_model = blend_models([model for _, model in tuned_models])
                variants.append(await _record_variant(
                    "blended_model", "Blended", blended_model, "blended", _cv_row(pull()),
                    parameters={
                        **results.estimator_spec(blended_model),
                        "members": [name for name, _ in tuned_models],
                    },
                ))
                final_name = "blended_model"
            except Exception as exc:
                logging.error(f"Blending failed: {exc}")
                warnings.append(f"blending failed: {exc}")
        if final_name is None and tuned_models:
            final_name = tuned_models[0][0]
        if final_name is None:
            variants.append(await _record_variant(
                "best_untuned", type(best[0]).__name__, best[0], "comparison_best", {}
            ))
            final_name = "best_untuned"
        for variant in variants:
            if variant["variant_key"] == final_name and variant["status"] == "ready":
                variant["selected"] = True

        # ── Record the comparison (one transaction) ────────────────────────
        comparison_payload = results.comparison_payload(
            prepared,
            preprocessing={
                "setup": {
                    key: value
                    for key, value in setup_kwargs.items()
                    if key not in ("data", "test_data", "experiment_name")
                },
            },
            features=[column for column in data.columns if column != target_column],
            split_definition={
                "kind": eval_kind,
                "fold": params.fold,
                "train_rows": int(len(setup_kwargs["data"])),
                "holdout_rows": holdout_rows,
                "stratify": params.data_split_stratify,
                "shuffle": params.data_split_shuffle,
            },
            seeds={"session_id": session_id, "derived_from_dataset_digest": params.session_id is None},
            variants=variants,
        )
        try:
            recorded = await domain_api.record_model_comparison(comparison, comparison_payload)
        except domain_api.DomainApiError as exc:
            # No file fallback — an unrecorded comparison did not happen.
            return json.dumps({
                "status": "error",
                "error_message": f"Model results could not be recorded: {exc}",
                "comparison_key": comparison,
                "feature_set_id": prepared.feature_set_id,
            }, indent=2)

        logging.info("Regression experiment complete.")
        return json.dumps(
            results.recorded_response(
                recorded,
                experiment_name=experiment_name,
                prepared=prepared,
                reused=False,
                figures=figures,
                warnings=warnings,
            ),
            indent=2,
        )

    except Exception as exc:
        logging.error(f"Unhandled error in pycaret_train_regressor: {exc}", exc_info=True)
        return json.dumps({
            "status": "error",
            "error_message": str(exc),
            "feature_set_id": params.feature_set_id,
            "input_path": params.input_path,
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
    """Load a recorded PyCaret regression model and predict on new data."""
    try:
        run_id = domain_api.require_run()
        output_dir = str(TEMP_DIR / "modeling" / run_id / "inference")
        os.makedirs(output_dir, exist_ok=True)
        log_file = os.path.join(output_dir, "pycaret_regression_inference.log")
        _setup_logging(log_file, verbose=params.verbose)

        logging.info(f"Inference | model: {params.model_path} | input: {params.input_path}")

        try:
            from pycaret.regression import load_model, predict_model
        except ImportError as exc:
            raise ImportError("PyCaret is required. Install with: pip install pycaret") from exc

        with open(params.input_path, "rb") as stream:
            input_bytes = stream.read()
        data = pd.read_csv(io.BytesIO(input_bytes))
        logging.info(f"Data loaded: {data.shape}")

        has_ground_truth = (
            params.ground_truth_column is not None
            and params.ground_truth_column in data.columns
        )
        if params.ground_truth_column and not has_ground_truth:
            logging.warning(f"Column '{params.ground_truth_column}' not found – metrics skipped.")

        model_path = params.model_path.removesuffix(".pkl")
        model_dir = os.path.dirname(model_path)
        if not os.path.exists(model_path) and not os.path.exists(model_path + ".pkl"):
            if os.path.isdir(model_dir):
                base = os.path.basename(model_path)
                candidates = [f for f in os.listdir(model_dir) if f.startswith(base)]
                if candidates:
                    model_path = os.path.join(model_dir, candidates[0]).removesuffix(".pkl")
                    logging.info(f"Resolved model path to: {model_path}")

        # Identify the file by the digest recorded for it, never trust it.
        base_variant = await results.recorded_variant_for(model_path)
        mismatch = results.model_file_mismatch(model_path, base_variant)
        if mismatch:
            return json.dumps({
                "status": "error",
                "error_message": mismatch,
                "model_path": params.model_path,
            }, indent=2)

        try:
            model = load_model(model_path)
            logging.info("Model loaded.")
        except FileNotFoundError as exc:
            return json.dumps({
                "status": "error",
                "error_message": f"Model file not found: {exc}",
                "model_path": params.model_path,
            }, indent=2)

        try:
            predictions = predict_model(model, data=data)
        except Exception as exc:
            logging.error(f"predict_model failed: {exc}")
            return json.dumps({
                "status": "error",
                "error_message": f"Failed to make predictions: {exc}",
            }, indent=2)

        input_digest = bytes_digest(input_bytes)
        base_key = (base_variant or {}).get("variant_key") or "unrecorded_model"
        artifact = await results.publish_predictions(
            predictions,
            run_id=run_id,
            comparison=(base_variant or {}).get("comparison_key") or "inference",
            variant=f"{base_key}__inference_{input_digest[:12]}",
            metadata={"input_digest": input_digest, "model_variant_key": base_key},
        )
        predictions_artifact_id = str(artifact["artifact_id"])

        metrics = None
        if has_ground_truth:
            scored = predictions.copy()
            scored["target"] = data[params.ground_truth_column].values
            metrics = _regression_metrics(scored, "target")

        recorded_evaluation = None
        warnings: list = []
        if metrics and base_variant is not None:
            recorded_evaluation = await results.record_independent_evaluation(
                base_variant=base_variant,
                input_bytes=input_bytes,
                input_name=os.path.basename(params.input_path),
                predictions_artifact_id=predictions_artifact_id,
                n_cases=len(predictions),
                metrics=metrics,
            )
        elif metrics:
            warnings.append(
                "The evaluation was not recorded: this model file is not a recorded model "
                "result of this session."
            )

        logging.info("Regression inference complete.")
        return json.dumps({
            "status": "success",
            "predictions_artifact_id": predictions_artifact_id,
            "exports": {"predictions": f"/artifacts/{predictions_artifact_id}/content?download=true"},
            "num_predictions": len(predictions),
            "has_ground_truth": has_ground_truth,
            "metrics": metrics,
            "recorded_evaluation": recorded_evaluation,
            "warnings": warnings,
            "input_path": params.input_path,
            "model_path": params.model_path,
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
