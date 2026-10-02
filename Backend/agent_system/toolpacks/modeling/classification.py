"""PyCaret MCP Tools."""

import io
import json
import logging
import os
from typing import Optional
import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .._toolkit import tool
from ..common.manifests import INTERIM_HEAVY
from ..radiomics.digests import bytes_digest
from ..common import domain_api
from ..common.workspace import WorkspacePath, get_workspace
from . import results
from .evaluation import (
    chart_data_from_recorded as _chart_data_from_recorded,
    holdout_metrics as _holdout_metrics,
    safe_float as _safe_float,
)
from .holdout import resolve_selection_holdout
from storage_paths import TEMP_DIR, workspace_subpath
from ..common.emit import ToolEvent, log_tool_state, TextPayload, ProgressPayload


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


# ── Input Models ───────────────────────────────────────────────────────────────

class TrainClassifierInput(BaseModel):
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
        default=5, description="Number of cross-validation folds."
    )
    session_id: Optional[int] = Field(
        default=None,
        description=(
            "Random seed. Derived from the input's digest when omitted, so a replay "
            "trains with the same seed."
        ),
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

    @model_validator(mode="after")
    def _one_source(self) -> "TrainClassifierInput":
        results.check_one_source(self.feature_set_id, self.input_path, self.holdout_path)
        return self


class RunInferenceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    input_path: WorkspacePath = Field(..., description="Path to CSV file containing rows to predict.")
    model_path: WorkspacePath = Field(..., description="Path to the saved PyCaret model.")
    ground_truth_column: Optional[str] = Field(default=None)
    prediction_filename: str = Field(default="predictions.csv", description="Display name of the predictions export.")
    verbose: bool = Field(default=True)


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool(
    name="pycaret_train_classifier",
    visibility={"agent"},
    idempotent=True,
    **INTERIM_HEAVY,
)
async def pycaret_train_classifier(params: TrainClassifierInput) -> str:
    """Compare, tune, and blend classification models with PyCaret."""
    logger = logging.getLogger(__name__)
    output_dir = None
    try:
        run_id = domain_api.require_run()
        work_root = str(TEMP_DIR / "modeling" / run_id)
        target_column = "target"

        # ── Input: a frozen feature set, or a captured generic CSV ─────────
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
            "task": "classification",
            "source": prepared.feature_set_id or prepared.dataset_digest,
            "seed": session_id,
            **{
                name: getattr(params, name)
                for name in (
                    "fold", "use_gpu", "fix_imbalance", "data_split_stratify",
                    "data_split_shuffle", "preprocess", "ignore_features",
                    "numeric_features", "categorical_features", "date_features",
                    "n_select", "normalize", "transformation", "pca",
                    "pca_components", "include_models", "exclude_models",
                )
            },
        }
        comparison = results.comparison_key("classification", prepared.source_label, training_config)
        experiment_name = params.experiment_name or comparison

        # ── Replay: a recorded comparison is read, not retrained ─────
        recorded = await domain_api.get_model_comparison(comparison)
        if recorded:
            return json.dumps(
                results.recorded_response(
                    recorded,
                    experiment_name=experiment_name,
                    prepared=prepared,
                    reused=True,
                    chart_data=_chart_data_from_recorded(recorded),
                ),
                indent=2,
            )

        # Logs, plots and PyCaret scratch are disposable (temp/); the results
        # are rows and artifacts, and the fitted pipelines live in the model
        # binary directory.
        output_dir = os.path.join(work_root, results.slug(comparison))
        os.makedirs(output_dir, exist_ok=True)
        log_file = os.path.join(output_dir, "pycaret_classification.log")
        _setup_logging(log_file)
        os.environ["TQDM_DISABLE"] = "1"

        logger.info(f"Experiment: {experiment_name} | comparison: {comparison} | target: {target_column}")

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

        data = pd.read_csv(prepared.train_path)
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
                "feature_set_id": prepared.feature_set_id,
                "rows": data.shape[0],
                "cols": data.shape[1],
            }, indent=2)

        if target_column not in data.columns:
            raise ValueError(f"Target column '{target_column}' not found in data.")

        if data[target_column].isna().any():
            return json.dumps({
                "status": "error",
                "error_message": "The target column contains missing values. Every training row needs a target label.",
                "feature_set_id": prepared.feature_set_id,
            }, indent=2)

        class_counts = data[target_column].value_counts()
        if len(class_counts) < 2:
            return json.dumps({
                "status": "error",
                "error_message": "Classification requires at least two target classes.",
                "feature_set_id": prepared.feature_set_id,
                "class_distribution": class_counts.to_dict(),
            }, indent=2)

        min_class_count = int(class_counts.min())
        n_rows = data.shape[0]

        # ── Adaptive dataset-size handling ───────────────────────────────
        SMOTE_MIN_SAMPLES = 7  # SMOTE default k_neighbors=5 → need ≥ 6 minority samples in train
        SMALL_COHORT_THRESHOLD = 30

        train_ratio = 0.8 if n_rows < 50 else 0.7
        # setup() takes its own train/test split (train_size, applied below
        # for n_rows < 50) *before* cross-validation ever runs, so the
        # minority-class count that actually bounds a valid fold count is the
        # one left in the training partition, not the full dataset's. Fold
        # count used to be clamped against the full-dataset min_class_count,
        # so a fold request could still exceed the training partition's
        # minority count (e.g. 5 folds requested, only 4 minority rows left
        # after an 0.8 train/test split) — StratifiedKFold rejects that with
        # n_splits > n_members per class, every candidate model failed
        # identically, and compare_models() came back with an empty results
        # table ("No models trained successfully") even though the cohort
        # itself had enough rows to model.
        is_small_cohort = n_rows < SMALL_COHORT_THRESHOLD
        estimated_train_minority = (
            max(1, int(min_class_count * train_ratio)) if is_small_cohort else min_class_count
        )
        requested_fold = max(2, params.fold)
        effective_fold = min(requested_fold, estimated_train_minority)
        if effective_fold < 2:
            return json.dumps({
                "status": "error",
                "error_message": (
                    f"The rarest class has only {min_class_count} sample(s) overall "
                    f"(~{estimated_train_minority} left in the training partition after "
                    "the train/test split). At least two samples per class in the "
                    "training partition are required for 2-fold cross-validation."
                ),
                "feature_set_id": prepared.feature_set_id,
                "class_distribution": class_counts.to_dict(),
                "fold": requested_fold,
            }, indent=2)

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

        y_holdout = None

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

        if n_rows < 50:
            setup_kwargs["train_size"] = 0.8
            logger.info("Using train_size=0.8 for small dataset (%d rows).", n_rows)

        # Honour the withheld rows: a feature set's own holdout (its
        # holdout_groups), or an explicit holdout CSV with a generic input. The
        # evaluated rows are then the rows whose labels never reached the
        # selector. Without this, PyCaret re-splits at random and puts
        # selection-contaminated rows back in the test half.
        selection_holdout_path = prepared.holdout_path
        selection_holdout_used = False
        holdout_rows = None
        if selection_holdout_path:
            try:
                holdout_df = pd.read_csv(selection_holdout_path)
                if target_column in holdout_df.columns and not holdout_df.empty:
                    train_df = data
                    if len(train_df) > len(holdout_df):
                        # `data` may be the full export; drop the holdout rows rather
                        # than training on them and then scoring them.
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
                        logger.info(
                            "Using the withheld rows (%d) as the evaluation set; "
                            "PyCaret will not re-split.",
                            len(holdout_df),
                        )
            except Exception as exc:
                logger.warning(
                    "Could not use the withheld rows (%s); falling back to PyCaret's "
                    "own split, which reintroduces selection leakage.",
                    exc,
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

        def _try_setup(kwargs):
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
                    "data": kwargs.get("data", data),
                    "target": target_column,
                    "session_id": session_id,
                    "fold": effective_fold,
                    "fix_imbalance": effective_fix_imbalance,
                }
                # Never silently drop the selection holdout on the retry path; losing
                # it here would reintroduce the leak with no signal in the output.
                if kwargs.get("test_data") is not None:
                    minimal["test_data"] = kwargs["test_data"]
                elif n_rows < 50:
                    minimal["train_size"] = 0.8
                logger.warning("Setup failed - retrying with minimal params.")
                return setup(**minimal)

        _try_setup(setup_kwargs)
        logger.info("PyCaret setup complete.")

        try:
            from pycaret.classification import get_config
            X_test = get_config("X_test")
            y_holdout = get_config("y_test")
        except Exception:
            pass

        eval_kind = results.evaluation_kind(prepared, selection_holdout_used)
        generalisation = results.generalisation_estimate(prepared, selection_holdout_used)

        # ── Stage 2: Compare models ─────────────────────────────────────────
        payload = ProgressPayload(current=2, total=TOTAL_STAGES, label="Comparing models")
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        compare_kwargs: dict = {"n_select": effective_n_select}
        if is_small_cohort and not params.include_models:
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
        logger.debug(f"compare_models result table:\n{compare_df.to_string()}")

        if not best:
            logger.error(f"best is empty. n_select={params.n_select}, "
                        f"models in results: {len(compare_df)}")
            raise Exception("No models trained successfully, dataset size is likely too small.")

        # Every compared candidate is a variant: its CV metrics are what the
        # retired comparison CSV used to hold.
        variants: list = []
        for model_id, row in compare_df.iterrows():
            variants.append({
                "variant_key": f"compare_{results.slug(model_id)}",
                "algorithm": str(row.get("Model", model_id)),
                "parameters": results.versioned({"pycaret_model_id": str(model_id)}),
                "metrics": results.versioned({
                    "stage": "comparison",
                    "fold": effective_fold,
                    "cv": {
                        str(column): _safe_float(value)
                        for column, value in row.items()
                        if column not in ("Model", "TT (Sec)")
                    },
                }),
                "selected": False,
                "status": "ready",
            })

        def _get_model_label(model) -> str:
            try:
                return type(model).__name__
            except Exception:
                return str(model)

        def _create_plots(model, plots_dir: str) -> None:
            plot_types = [
                "auc", "confusion_matrix", "class_report", "feature",
                "pr", "error", "learning", "calibration",
            ]
            original_dir = os.getcwd()
            try:
                os.chdir(plots_dir)
                for pt in plot_types:
                    try:
                        plot_model(model, plot=pt, save=True)
                    except Exception as exc:
                        logger.warning(f"Plot '{pt}' skipped: {exc}")
            finally:
                os.chdir(original_dir)

        def _create_interpretations(model, plots_dir: str) -> None:
            original_dir = os.getcwd()
            try:
                os.chdir(plots_dir)
                for plot_type in ("summary", "reason"):
                    try:
                        kwargs = {"plot": plot_type, "save": True}
                        if plot_type == "reason":
                            kwargs["observation"] = 1
                        interpret_model(model, **kwargs)
                    except Exception as exc:
                        logger.warning(f"Interpretation '{plot_type}' skipped: {exc}")
            finally:
                os.chdir(original_dir)

        def _eval_on_holdout(model, name: str):
            """Predict the evaluation rows; return (predictions, chart_dict)."""
            try:
                # Force raw_score=True so predictions contain full probabilities
                try:
                    preds = predict_model(model, raw_score=True)
                except Exception:
                    preds = predict_model(model)
                chart_info = _holdout_metrics(preds, y_holdout) if y_holdout is not None else None
                return preds, chart_info
            except Exception as exc:
                logger.error(f"Holdout eval failed for '{name}': {exc}")
                return None, None

        figures: list = []
        warnings: list = []

        async def _record_variant(name, label, model, stage, cv_metrics, parameters=None):
            """Save the pipeline, publish its figures and predictions, and
            describe the variant for the comparison record."""
            model_file = None
            base = results.model_file_base(get_workspace(), comparison, name)
            try:
                save_model(model, base)
                model_file = {
                    "name": results.model_file_logical_name(comparison, name),
                    "digest": results.file_digest(base + ".pkl"),
                }
            except Exception as exc:
                logger.error(f"Could not save {name}: {exc}")
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

            preds, chart_info = _eval_on_holdout(model, name)
            predictions_file_id = None
            if preds is not None:
                artifact = await results.publish_predictions(
                    preds,
                    run_id=run_id,
                    comparison=comparison,
                    variant=name,
                    metadata={"variant_key": name, "evaluation": eval_kind},
                )
                predictions_file_id = str(artifact["file_id"])
            evaluation = None
            if chart_info is not None:
                evaluation = {
                    "kind": eval_kind,
                    "n_cases": int(len(preds)),
                    "generalisation_estimate": generalisation,
                    "metrics": chart_info.get("metrics", {}),
                    "roc_curve": chart_info.get("roc_curve"),
                    "pr_curve": chart_info.get("pr_curve"),
                }
            return {
                "variant_key": name,
                "algorithm": label,
                "parameters": results.versioned(parameters or results.estimator_spec(model)),
                "metrics": results.versioned({
                    "stage": stage,
                    "fold": effective_fold,
                    "cv": cv_metrics,
                    "evaluation": evaluation,
                }),
                "predictions_file_id": predictions_file_id,
                "model_file_name": model_file["name"] if model_file else None,
                "model_file_digest": model_file["digest"] if model_file else None,
                "selected": False,
                "status": "ready",
            }

        tuned_models: list = []

        # ── Stages 3…(2+N): Tune, evaluate & plot each model ───────────────
        for i, model in enumerate(best):
            name = f"tuned_model_{i + 1}"
            label = _get_model_label(model)
            logger.info(f"Tuning {name} ({i + 1}/{len(best)})")

            payload = ProgressPayload(
                current=3 + i,
                total=TOTAL_STAGES,
                label=f"Tuning & evaluating {label} ({i + 1}/{len(best)})",
            )
            log_tool_state(ToolEvent("tool_progress", "progress", payload))

            try:
                tuned = tune_model(model)
                cv_df = pull()
                if "Mean" in cv_df.index:
                    cv_metrics = {str(col): _safe_float(cv_df.loc["Mean", col])
                                  for col in cv_df.columns if col != "TT (Sec)"}
                else:
                    last = cv_df.iloc[-1]
                    cv_metrics = {str(k): _safe_float(v) for k, v in last.items() if k != "TT (Sec)"}
                variants.append(await _record_variant(name, label, tuned, "tuned", cv_metrics))
                tuned_models.append((name, tuned))
            except Exception as exc:
                logger.error(f"Failed to process {name}: {exc}")
                variants.append({
                    "variant_key": name,
                    "algorithm": label,
                    "parameters": results.versioned(results.estimator_spec(model)),
                    "metrics": results.versioned({"stage": "tuned", "error": str(exc)[:500]}),
                    "selected": False,
                    "status": "failed",
                })

        # ── Final stage: Blend (if applicable) + select the final model ────
        payload = ProgressPayload(
            current=TOTAL_STAGES,
            total=TOTAL_STAGES,
            label="Blending models & recording results" if len(tuned_models) > 1 else "Recording results",
        )
        log_tool_state(ToolEvent("tool_progress", "progress", payload))

        final_name = None
        if len(tuned_models) > 1:
            try:
                blended_model = blend_models([model for _, model in tuned_models])
                blend_cv = pull()
                blend_cv_metrics = {}
                if "Mean" in blend_cv.index:
                    blend_cv_metrics = {str(col): _safe_float(blend_cv.loc["Mean", col])
                                        for col in blend_cv.columns if col != "TT (Sec)"}
                blend_parameters = {
                    **results.estimator_spec(blended_model),
                    "members": [name for name, _ in tuned_models],
                }
                variants.append(await _record_variant(
                    "blended_model", "Blended", blended_model, "blended", blend_cv_metrics,
                    parameters=blend_parameters,
                ))
                final_name = "blended_model"
            except Exception as exc:
                logger.error(f"Blending failed: {exc}")
                warnings.append(f"blending failed: {exc}")
        if final_name is None and tuned_models:
            final_name = tuned_models[0][0]
        if final_name is None:
            # Every tuning failed: the best compared candidate, untuned.
            variants.append(await _record_variant(
                "best_untuned", _get_model_label(best[0]), best[0], "comparison_best", {}
            ))
            final_name = "best_untuned"
        for variant in variants:
            if variant["variant_key"] == final_name and variant["status"] == "ready":
                variant["selected"] = True

        # ── Record the comparison (one transaction) ────────────────────────
        preprocessing = {
            "setup": {
                key: value
                for key, value in setup_kwargs.items()
                if key not in ("data", "test_data", "experiment_name")
            },
            "requested_fix_imbalance": params.fix_imbalance,
            "effective_fix_imbalance": effective_fix_imbalance,
        }
        split_definition = {
            "kind": eval_kind,
            "fold": effective_fold,
            "train_rows": int(len(setup_kwargs["data"])),
            "holdout_rows": holdout_rows,
            "train_size": setup_kwargs.get("train_size"),
            "stratify": params.data_split_stratify,
            "shuffle": params.data_split_shuffle,
        }
        comparison_payload = results.comparison_payload(
            prepared,
            preprocessing=preprocessing,
            features=[column for column in data.columns if column != target_column],
            split_definition=split_definition,
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

        logger.info("Classification experiment complete.")
        payload = TextPayload(message="Classification Experiment...Done")
        log_tool_state(ToolEvent("tool_finished", "text", payload))

        return json.dumps(
            results.recorded_response(
                recorded,
                experiment_name=experiment_name,
                prepared=prepared,
                reused=False,
                figures=figures,
                warnings=warnings,
                chart_data=_chart_data_from_recorded(recorded),
            ),
            indent=2,
        )

    except Exception as exc:
        logger.error(f"Unhandled error in pycaret_train_classifier: {exc}", exc_info=True)
        return json.dumps({
            "status": "error",
            "error_message": str(exc),
            "feature_set_id": params.feature_set_id,
            "input_path": params.input_path,
        }, indent=2)


@tool(
    name="pycaret_run_inference",
    visibility={"agent"},
    idempotent=True,
    **INTERIM_HEAVY,
)
async def pycaret_run_inference(params: RunInferenceInput) -> str:
    """Load a recorded PyCaret model and generate predictions on new data."""
    logger = logging.getLogger(__name__)
    try:
        import numpy as np
        from sklearn.metrics import (
            accuracy_score, confusion_matrix, f1_score,
            precision_score, recall_score, roc_auc_score,
        )

        run_id = domain_api.require_run()
        output_dir = str(TEMP_DIR / "modeling" / run_id / "inference")
        os.makedirs(output_dir, exist_ok=True)
        log_file = os.path.join(output_dir, "pycaret_inference.log")
        _setup_logging(log_file, verbose=params.verbose)

        logger.info(f"Inference | model: {params.model_path} | input: {params.input_path}")

        try:
            from pycaret.classification import load_model, predict_model
        except ImportError as exc:
            raise ImportError("PyCaret is required. Install with: pip install pycaret") from exc

        with open(params.input_path, "rb") as stream:
            input_bytes = stream.read()
        data = pd.read_csv(io.BytesIO(input_bytes))
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
        except FileNotFoundError as exc:
            return json.dumps({"status": "error", "error_message": f"Model file not found: {exc}"}, indent=2)

        try:
            predictions = predict_model(model, data=data, raw_score=True)
        except Exception:
            predictions = predict_model(model, data=data)

        input_digest = bytes_digest(input_bytes)
        base_key = (base_variant or {}).get("variant_key") or "unrecorded_model"
        artifact = await results.publish_predictions(
            predictions,
            run_id=run_id,
            comparison=(base_variant or {}).get("comparison_key") or "inference",
            variant=f"{base_key}__inference_{input_digest[:12]}",
            metadata={"input_digest": input_digest, "model_variant_key": base_key},
        )
        predictions_file_id = str(artifact["file_id"])

        metrics: dict = {}
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
            metrics["confusion_matrix"] = cm.tolist()
            metrics["confusion_matrix_labels"] = sorted(str(c) for c in unique_classes)

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

        recorded_evaluation = None
        warnings: list = []
        if metrics and base_variant is not None:
            recorded_evaluation = await results.record_independent_evaluation(
                base_variant=base_variant,
                input_bytes=input_bytes,
                input_name=os.path.basename(params.input_path),
                predictions_file_id=predictions_file_id,
                n_cases=len(predictions),
                metrics=metrics,
            )
        elif metrics:
            warnings.append(
                "The evaluation was not recorded: this model file is not a recorded model "
                "result of this session."
            )

        return json.dumps({
            "status": "success",
            "predictions_artifact_id": predictions_file_id,  # tool result key, kept (plan.md: no success result shape changes)
            "exports": {"predictions": f"/files/{predictions_file_id}/content?download=true"},
            "num_predictions": len(predictions),
            "has_ground_truth": has_ground_truth,
            "metrics": results.json_safe(metrics) or None,
            "recorded_evaluation": recorded_evaluation,
            "warnings": warnings,
            "input_path": params.input_path,
            "model_path": params.model_path,
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