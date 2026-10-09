"""Training one comparison on the engine — the body of ``train_classifier``
and ``train_regressor``.

1. the input (a frozen feature set, or a captured CSV), its seed and the
   comparison's deterministic key; a recorded comparison is replayed, not retrained;
2. the data, checked, split into training and evaluation rows (the feature
   set's withheld rows when it has them) and the folds;
3. compare the candidates by cross-validation, tune the top ``n_select``,
   blend the tuned models, and select the final one;
4. every kept variant: its fitted pipeline under the model binary directory,
   its figures and evaluation predictions as artifacts;
5. the comparison, as ``model_results`` rows in one write.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd
from pydantic import BaseModel

from ..common import domain_api, settings
from ..common.emit import emit, emit_progress
from ..common.failures import ToolFailure, fail
from ..common.values import json_safe, str_list
from ..common.workspace import get_workspace
from . import results
from .engine import blend, compare, cv, dataset, estimators, fitting, persistence, plots, tune
from .engine.preprocessing import TARGET, PreprocessingConfig, build_pipeline, pipeline_document
from .evaluation import chart_data_from_recorded, holdout_metrics, regression_metrics
from .holdout import resolve_selection_holdout
from storage_paths import TEMP_DIR

logger = logging.getLogger(__name__)

MIN_ROWS = 6
SMALL_COHORT_ROWS = 30
SMALL_COHORT_N_SELECT = 2
# Arguments that name the input or label the run; everything else is configuration.
_NOT_CONFIGURATION = {"feature_set_id", "input_path", "holdout_path", "experiment_name", "session_id"}


def csv_list(value: Optional[str]) -> Optional[list[str]]:
    return str_list(value)


def preprocessing_config(params: BaseModel) -> PreprocessingConfig:
    return PreprocessingConfig.from_mapping(params.model_dump())


def split_rows(
    task: str,
    data: pd.DataFrame,
    holdout_path: Optional[str],
    *,
    seed: int,
    stratify: bool,
    shuffle: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, bool, Optional[float]]:
    """(train, evaluation rows, whether they are withheld rows, train size of
    a random split). Withheld rows are the feature set's own holdout or an
    explicit holdout CSV; without them the rows are split at random."""
    if holdout_path:
        try:
            holdout = pd.read_csv(holdout_path)
        except Exception as exc:
            logger.warning("could not read the withheld rows (%s); splitting at random", exc)
            holdout = None
        if holdout is not None and TARGET in holdout.columns and not holdout.empty:
            train = data
            if len(train) > len(holdout):
                # `data` may be the full export: never train on the rows it scores.
                merged = train.merge(holdout.drop_duplicates(), how="left", indicator=True)
                if (merged["_merge"] == "both").any():
                    train = train[merged["_merge"].to_numpy() == "left_only"]
            if not train.empty:
                return train.reset_index(drop=True), holdout.reset_index(drop=True), True, None
    train, holdout, size = cv.random_split(
        task, data, target=TARGET, seed=seed, stratify=stratify, shuffle=shuffle
    )
    return train.reset_index(drop=True), holdout.reset_index(drop=True), False, size


@dataclass
class _Run:
    """One comparison being trained: its input, its data and what it produced."""

    task: str
    params: BaseModel
    run_id: str
    prepared: results.ModelingInput
    seed: int
    comparison: str
    experiment_name: str
    # Set by _setup:
    data: dataset.Dataset | None = None
    train_rows: int = 0
    eval_rows: int = 0
    holdout_used: bool = False
    train_size: float | None = None
    folds: int = 0
    splitter: Any = None
    candidates: list[estimators.Estimator] = field(default_factory=list)
    n_select: int = 1
    balance: bool = False
    n_jobs: int = 1
    # Produced:
    variants: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    figures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def failure(self) -> dict[str, Any]:
        """What every error result of this comparison names."""
        return {"feature_set_id": self.prepared.feature_set_id, "comparison_key": self.comparison}

    @property
    def output_dir(self) -> str:
        return str(TEMP_DIR / "modeling" / self.run_id / results.slug(self.comparison))

    @property
    def eval_kind(self) -> str:
        return results.evaluation_kind(self.prepared, self.holdout_used)

    @property
    def generalisation(self) -> bool:
        return results.generalisation_estimate(self.prepared, self.holdout_used)

    def make_pipeline(self, entry: estimators.Estimator) -> Any:
        model = estimators.instantiate(entry, seed=self.seed, n_jobs=self.n_jobs)
        return build_pipeline(model, self.data.columns, preprocessing_config(self.params))


# ----------------------------------------------------------------------
# 1. The input
# ----------------------------------------------------------------------
async def _prepare(task: str, params: BaseModel) -> _Run:
    run_id = domain_api.require_run()
    try:
        prepared = await results.prepare_input(
            feature_set_id=params.feature_set_id,
            input_path=params.input_path,
            holdout_path=(
                resolve_selection_holdout(params.input_path, params.holdout_path)
                if params.input_path
                else None
            ),
            work_dir=str(TEMP_DIR / "modeling" / run_id / "input" / results.slug(params.feature_set_id or "csv")),
        )
    except domain_api.DomainApiError as exc:
        raise ToolFailure(domain_api.error_result_from(exc, "the training input could not be prepared"))
    except ValueError as exc:
        raise fail("invalid_input", str(exc), feature_set_id=params.feature_set_id)

    seed = params.session_id if params.session_id is not None else results.deterministic_seed(prepared.dataset_digest)
    comparison = results.comparison_key(
        task,
        prepared.source_label,
        {
            "task": task,
            "engine": persistence.ENGINE,
            "source": prepared.feature_set_id or prepared.dataset_digest,
            "seed": seed,
            **params.model_dump(exclude=_NOT_CONFIGURATION),
        },
    )
    return _Run(
        task=task,
        params=params,
        run_id=run_id,
        prepared=prepared,
        seed=seed,
        comparison=comparison,
        experiment_name=params.experiment_name or comparison,
    )


# ----------------------------------------------------------------------
# 2. The data
# ----------------------------------------------------------------------
def _checked_data(run: _Run) -> pd.DataFrame:
    """The training data, refused when it cannot train this task."""
    data = pd.read_csv(run.prepared.train_path)
    if len(data) < MIN_ROWS:
        raise fail(
            "insufficient_data",
            f"The dataset has only {len(data)} rows, too few for cross-validated training; "
            f"at least {MIN_ROWS} are required.",
            rows=int(len(data)), cols=int(data.shape[1]), **run.failure,
        )
    if TARGET not in data.columns:
        raise fail("missing_target", f"The data has no '{TARGET}' column.", **run.failure)
    if data[TARGET].isna().any():
        raise fail(
            "missing_target",
            "The target column contains missing values. Every training row needs a target.",
            **run.failure,
        )
    if run.task == estimators.CLASSIFICATION:
        counts = data[TARGET].value_counts()
        if len(counts) < 2:
            raise fail(
                "insufficient_classes",
                "Classification requires at least two target classes.",
                class_distribution=json_safe(counts.to_dict()), **run.failure,
            )
    elif not pd.api.types.is_numeric_dtype(data[TARGET]):
        raise fail("invalid_target", "Regression needs a numeric target column.", **run.failure)
    return data


def _setup(run: _Run) -> None:
    """Split the rows, build the dataset and the folds, and choose the candidates."""
    params = run.params
    data = _checked_data(run)
    train, holdout, run.holdout_used, run.train_size = split_rows(
        run.task, data, run.prepared.holdout_path, seed=run.seed,
        stratify=params.data_split_stratify, shuffle=params.data_split_shuffle,
    )
    run.train_rows, run.eval_rows = len(train), len(holdout)
    try:
        run.data = dataset.build(run.task, train, holdout, preprocessing_config(params))
    except ValueError as exc:
        raise fail("invalid_input", str(exc), **run.failure)

    run.folds = cv.clamp_folds(run.task, params.fold, pd.Series(run.data.y_train))
    if run.folds < 2:
        raise fail(
            "insufficient_data",
            "The rarest class has fewer than two rows in the training partition, so "
            "2-fold cross-validation is impossible.",
            class_distribution=json_safe(train[TARGET].value_counts().to_dict()),
            fold=int(params.fold), **run.failure,
        )

    small_cohort = run.task == estimators.CLASSIFICATION and len(data) < SMALL_COHORT_ROWS
    run.n_select = max(1, int(params.n_select))
    if small_cohort:
        run.n_select = min(run.n_select, SMALL_COHORT_N_SELECT)
    try:
        run.candidates = estimators.select(
            run.task,
            include=csv_list(params.include_models),
            exclude=csv_list(params.exclude_models),
            small_cohort=small_cohort,
        )
    except ValueError as exc:
        raise fail("invalid_model_id", str(exc), **run.failure)
    if not run.candidates:
        raise fail("no_candidates", "No candidate model is left to compare.", **run.failure)

    if getattr(params, "use_gpu", False):
        run.warnings.append("use_gpu is not supported by the scikit-learn engine; trained on the CPU")
    run.balance = bool(getattr(params, "fix_imbalance", False)) and run.task == estimators.CLASSIFICATION
    run.n_jobs = settings.compute_cpu_threads()
    run.splitter = cv.splitter(
        run.task, run.folds, run.seed, shuffle=params.data_split_shuffle, stratify=params.data_split_stratify
    )
    logger.info(
        "experiment %s | comparison %s | %d train / %d evaluation rows | %d folds | %d candidates",
        run.experiment_name, run.comparison, run.train_rows, run.eval_rows, run.folds, len(run.candidates),
    )


# ----------------------------------------------------------------------
# 4. One kept variant
# ----------------------------------------------------------------------
async def _record_variant(
    run: _Run, name: str, label: str, model: Any, stage: str, cv_metrics: dict, parameters: dict, *, tree: bool = False
) -> dict[str, Any]:
    """Save the fitted pipeline, publish its figures and evaluation
    predictions, and describe the variant for the comparison record."""
    ds = run.data
    model_file = None
    base = results.model_file_base(get_workspace(), run.comparison, name)
    try:
        bundle = persistence.make_bundle(run.task, model, ds.features, ds.classes)
        await asyncio.to_thread(persistence.save_bundle, bundle, base + ".pkl")
        model_file = {
            "name": results.model_file_logical_name(run.comparison, name),
            "digest": results.file_digest(base + ".pkl"),
        }
    except Exception as exc:
        logger.error("could not save %s: %s", name, exc)
        run.warnings.append(f"{name}: the fitted pipeline could not be saved ({exc})")

    plots_dir = os.path.join(run.output_dir, results.slug(name), "plots")
    _, skipped = await asyncio.to_thread(
        plots.draw_all, run.task, model, ds.X_eval, ds.y_eval, plots_dir,
        classes=ds.classes, tree=tree, seed=run.seed,
    )
    for reason in skipped:
        logger.info("%s: figure skipped (%s)", name, reason)
    published, figure_warnings = await results.publish_figures(
        plots_dir, run_id=run.run_id, comparison=run.comparison, variant=name
    )
    run.figures.extend(published)
    run.warnings.extend(figure_warnings)

    predictions = await asyncio.to_thread(
        dataset.predictions_frame, run.task, model, ds.eval_frame, ds.features, ds.classes
    )
    artifact = await results.publish_predictions(
        predictions,
        run_id=run.run_id,
        comparison=run.comparison,
        variant=name,
        metadata={"variant_key": name, "evaluation": run.eval_kind},
    )
    evaluation: dict[str, Any] = {
        "kind": run.eval_kind,
        "n_cases": int(len(predictions)),
        "generalisation_estimate": run.generalisation,
    }
    if run.task == estimators.CLASSIFICATION:
        scored = holdout_metrics(predictions, predictions[TARGET])
        evaluation.update(metrics=scored["metrics"], roc_curve=scored["roc_curve"], pr_curve=scored["pr_curve"])
    else:
        evaluation["metrics"] = regression_metrics(predictions, TARGET) or {}
    return {
        "variant_key": name,
        "algorithm": label,
        "parameters": results.versioned(parameters),
        "metrics": results.versioned({"stage": stage, "fold": run.folds, "cv": cv_metrics, "evaluation": evaluation}),
        "predictions_file_id": str(artifact["file_id"]),
        "model_file_name": model_file["name"] if model_file else None,
        "model_file_digest": model_file["digest"] if model_file else None,
        "selected": False,
        "status": "ready",
    }


# ----------------------------------------------------------------------
# 3. Compare, tune, blend, select
# ----------------------------------------------------------------------
def _cv_of(row: dict) -> dict:
    return {key: value for key, value in row.items() if key not in ("model_id", "Model", "TT (Sec)")}


async def _compare(run: _Run, total_stages: int) -> list[dict[str, Any]]:
    """The comparison table, best first; every compared candidate is a variant."""
    ds = run.data
    emit_progress(current=2, total=total_stages, label=f"Comparing {len(run.candidates)} models")
    compared = await asyncio.to_thread(
        compare.compare, run.task, run.candidates, ds.X_train, ds.y_train,
        cv=run.splitter, make_pipeline=run.make_pipeline, balance=run.balance, n_classes=ds.n_classes,
    )
    for model_id, reason in compared.failures.items():
        run.warnings.append(f"{model_id}: excluded from the comparison ({reason})")
    if not compared.rows:
        raise fail(
            "no_model_trained",
            "No candidate model could be cross-validated; the dataset is likely too small.",
            failures=compared.failures, **run.failure,
        )
    run.variants += [
        {
            "variant_key": f"compare_{results.slug(row['model_id'])}",
            "algorithm": row["Model"],
            "parameters": results.versioned({"model_id": row["model_id"]}),
            "metrics": results.versioned({"stage": "comparison", "fold": run.folds, "cv": _cv_of(row)}),
            "selected": False,
            "status": "ready",
        }
        for row in compared.rows
    ]
    return compared.rows


async def _tune_top(run: _Run, rows: list[dict[str, Any]], total_stages: int) -> list[tuple[str, Any]]:
    """Tune and evaluate the top models; ``(variant key, unfitted tuned pipeline)``
    of each that succeeded. One that fails is recorded as a failed variant."""
    ds = run.data
    top = rows[: run.n_select]
    tuned_models: list[tuple[str, Any]] = []
    for index, row in enumerate(top):
        name = f"tuned_model_{index + 1}"
        entry = estimators.entry(run.task, row["model_id"])
        emit_progress(
            current=3 + index, total=total_stages, label=f"Tuning & evaluating {entry.name} ({index + 1}/{len(top)})"
        )
        try:
            tuned = await asyncio.to_thread(
                tune.tune, run.task, run.make_pipeline(entry), entry, ds.X_train, ds.y_train,
                cv=run.splitter, seed=run.seed, base_cv=_cv_of(row), balance=run.balance, n_classes=ds.n_classes,
            )
            fitted = await asyncio.to_thread(fitting.fit, tuned.model, ds.X_train, ds.y_train, balance=run.balance)
            parameters = {
                **results.estimator_spec(fitted),
                "model_id": entry.model_id,
                "tuning": {"improved": tuned.improved, "tried": tuned.tried, "params": tuned.params},
            }
            run.variants.append(
                await _record_variant(run, name, entry.name, fitted, "tuned", tuned.cv, parameters, tree=entry.tree)
            )
            tuned_models.append((name, tuned.model))
        except Exception as exc:
            logger.error("could not tune %s: %s", name, exc, exc_info=True)
            run.variants.append({
                "variant_key": name,
                "algorithm": entry.name,
                "parameters": results.versioned({"model_id": entry.model_id}),
                "metrics": results.versioned({"stage": "tuned", "error": str(exc)[:500]}),
                "selected": False,
                "status": "failed",
            })
    return tuned_models


async def _blend(run: _Run, tuned_models: list[tuple[str, Any]]) -> str | None:
    """The blended variant's key, or ``None`` when there is nothing (or it failed) to blend."""
    if len(tuned_models) < 2:
        return None
    ds = run.data
    try:
        blended = blend.blend(run.task, tuned_models)
        blend_cv = await asyncio.to_thread(
            fitting.cv_scores, run.task, blended, ds.X_train, ds.y_train, run.splitter, n_classes=ds.n_classes
        )
        fitted = await asyncio.to_thread(fitting.fit, blended, ds.X_train, ds.y_train)
        run.variants.append(await _record_variant(
            run, "blended_model", "Blended", fitted, "blended", blend_cv,
            {
                **results.estimator_spec(fitted),
                "members": [name for name, _ in tuned_models],
                "voting": getattr(fitted, "voting", None),
            },
        ))
        return "blended_model"
    except Exception as exc:
        logger.error("blending failed: %s", exc, exc_info=True)
        run.warnings.append(f"blending failed: {exc}")
        return None


async def _untuned_best(run: _Run, best: dict[str, Any]) -> str:
    """Every tuning failed: the best compared candidate, untuned."""
    entry = estimators.entry(run.task, best["model_id"])
    fitted = await asyncio.to_thread(
        fitting.fit, run.make_pipeline(entry), run.data.X_train, run.data.y_train, balance=run.balance
    )
    run.variants.append(await _record_variant(
        run, "best_untuned", entry.name, fitted, "comparison_best", _cv_of(best),
        {**results.estimator_spec(fitted), "model_id": entry.model_id}, tree=entry.tree,
    ))
    return "best_untuned"


def _select(run: _Run, final_name: str) -> None:
    for variant in run.variants:
        if variant["variant_key"] == final_name and variant["status"] == "ready":
            variant["selected"] = True


# ----------------------------------------------------------------------
# 5. The record
# ----------------------------------------------------------------------
async def _record_comparison(run: _Run) -> dict[str, Any]:
    params, ds = run.params, run.data
    preprocessing: dict[str, Any] = {"pipeline": pipeline_document(ds.columns, preprocessing_config(params))}
    if run.task == estimators.CLASSIFICATION:
        preprocessing.update(
            class_balance={
                "requested": bool(params.fix_imbalance),
                "effective": run.balance,
                "method": "balanced_sample_weight" if run.balance else None,
            },
            requested_fix_imbalance=bool(params.fix_imbalance),
            effective_fix_imbalance=run.balance,
        )
    payload = results.comparison_payload(
        run.prepared,
        preprocessing=preprocessing,
        features=ds.features,
        split_definition={
            "kind": run.eval_kind,
            "fold": run.folds,
            "train_rows": run.train_rows,
            "holdout_rows": run.eval_rows if run.holdout_used else None,
            "eval_rows": run.eval_rows,
            "train_size": run.train_size,
            "stratify": params.data_split_stratify,
            "shuffle": params.data_split_shuffle,
        },
        seeds={"session_id": run.seed, "derived_from_dataset_digest": params.session_id is None},
        variants=run.variants,
    )
    try:
        return await domain_api.record_model_comparison(run.comparison, payload)
    except domain_api.DomainApiError as exc:
        # No file fallback — an unrecorded comparison did not happen.
        raise ToolFailure(domain_api.error_result_from(exc, "the model results could not be recorded", **run.failure))


def _reply(run: _Run, recorded: dict[str, Any], *, reused: bool) -> str:
    return json.dumps(
        results.recorded_response(
            recorded,
            experiment_name=run.experiment_name,
            prepared=run.prepared,
            reused=reused,
            figures=run.figures,
            warnings=run.warnings,
            chart_data=chart_data_from_recorded(recorded),
        ),
        indent=2,
    )


async def _train(task: str, params: BaseModel) -> str:
    run = await _prepare(task, params)
    # A replay reads the recorded comparison instead of training again.
    recorded = await domain_api.get_model_comparison(run.comparison)
    if recorded:
        return _reply(run, recorded, reused=True)

    total_stages = 3 + max(1, int(params.n_select)) + 1  # load | compare | tune×N | blend + record
    emit("tool_log", f"Starting {task} experiment '{run.experiment_name}'...")
    emit_progress(current=1, total=total_stages, label="Loading data & building the preprocessing pipeline")
    await asyncio.to_thread(_setup, run)

    rows = await _compare(run, total_stages)
    tuned_models = await _tune_top(run, rows, total_stages)
    emit_progress(
        current=total_stages,
        total=total_stages,
        label="Blending models & recording results" if len(tuned_models) > 1 else "Recording results",
    )
    final_name = await _blend(run, tuned_models)
    if final_name is None and tuned_models:
        final_name = tuned_models[0][0]
    if final_name is None:
        final_name = await _untuned_best(run, rows[0])
    _select(run, final_name)

    recorded = await _record_comparison(run)
    emit("tool_log", f"{task.capitalize()} experiment...Done")
    return _reply(run, recorded, reused=False)


async def train(task: str, params: BaseModel) -> str | dict[str, Any]:
    """Run (or replay) one comparison; the tool's reply."""
    try:
        return await _train(task, params)
    except ToolFailure as failure:
        return failure.result
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, "the comparison could not be trained")


__all__ = ["csv_list", "preprocessing_config", "split_rows", "train"]
