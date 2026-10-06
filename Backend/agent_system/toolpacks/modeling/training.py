"""Training one comparison on the engine — the body of ``train_classifier``
and ``train_regressor``.

Compare the candidates by cross-validation, tune the top ``n_select``, blend
the tuned models, evaluate every kept variant on the evaluation rows (the
feature set's withheld rows when it has them), and record the comparison as
``model_results`` rows in one write. The fitted pipelines are saved under the
model binary directory, the figures and predictions are published as
artifacts, and a replay of the same comparison reads the recorded rows
instead of training again.
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

from .._toolkit import error_result
from ..common import domain_api, settings
from ..common.emit import emit, emit_progress
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
    if value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return None


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
class _Experiment:
    """What every variant of one comparison shares."""

    task: str
    run_id: str
    comparison: str
    output_dir: str
    data: dataset.Dataset
    seed: int
    eval_kind: str
    generalisation: bool
    folds: int
    figures: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    async def record_variant(
        self,
        name: str,
        label: str,
        model: Any,
        stage: str,
        cv_metrics: dict,
        parameters: dict,
        *,
        tree: bool = False,
    ) -> dict:
        """Save the fitted pipeline, publish its figures and evaluation
        predictions, and describe the variant for the comparison record."""
        ds = self.data
        model_file = None
        base = results.model_file_base(get_workspace(), self.comparison, name)
        try:
            bundle = persistence.make_bundle(self.task, model, ds.features, ds.classes)
            await asyncio.to_thread(persistence.save_bundle, bundle, base + ".pkl")
            model_file = {
                "name": results.model_file_logical_name(self.comparison, name),
                "digest": results.file_digest(base + ".pkl"),
            }
        except Exception as exc:
            logger.error("could not save %s: %s", name, exc)
            self.warnings.append(f"{name}: the fitted pipeline could not be saved ({exc})")

        plots_dir = os.path.join(self.output_dir, results.slug(name), "plots")
        _, skipped = await asyncio.to_thread(
            plots.draw_all, self.task, model, ds.X_eval, ds.y_eval, plots_dir,
            classes=ds.classes, tree=tree, seed=self.seed,
        )
        for reason in skipped:
            logger.info("%s: figure skipped (%s)", name, reason)
        published, figure_warnings = await results.publish_figures(
            plots_dir, run_id=self.run_id, comparison=self.comparison, variant=name
        )
        self.figures.extend(published)
        self.warnings.extend(figure_warnings)

        predictions = await asyncio.to_thread(
            dataset.predictions_frame, self.task, model, ds.eval_frame, ds.features, ds.classes
        )
        artifact = await results.publish_predictions(
            predictions,
            run_id=self.run_id,
            comparison=self.comparison,
            variant=name,
            metadata={"variant_key": name, "evaluation": self.eval_kind},
        )
        evaluation: dict[str, Any] = {
            "kind": self.eval_kind,
            "n_cases": int(len(predictions)),
            "generalisation_estimate": self.generalisation,
        }
        if self.task == estimators.CLASSIFICATION:
            scored = holdout_metrics(predictions, predictions[TARGET])
            evaluation.update(
                metrics=scored["metrics"], roc_curve=scored["roc_curve"], pr_curve=scored["pr_curve"]
            )
        else:
            evaluation["metrics"] = regression_metrics(predictions, TARGET) or {}
        return {
            "variant_key": name,
            "algorithm": label,
            "parameters": results.versioned(parameters),
            "metrics": results.versioned({
                "stage": stage,
                "fold": self.folds,
                "cv": cv_metrics,
                "evaluation": evaluation,
            }),
            "predictions_file_id": str(artifact["file_id"]),
            "model_file_name": model_file["name"] if model_file else None,
            "model_file_digest": model_file["digest"] if model_file else None,
            "selected": False,
            "status": "ready",
        }


def _cv_of(row: dict) -> dict:
    return {key: value for key, value in row.items() if key not in ("model_id", "Model", "TT (Sec)")}


async def train(task: str, params: BaseModel) -> str | dict[str, Any]:
    """Run (or replay) one comparison; the tool's reply."""
    run_id = domain_api.require_run()
    work_root = str(TEMP_DIR / "modeling" / run_id)
    try:
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
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, "the training input could not be prepared")
    except ValueError as exc:
        return error_result("invalid_input", str(exc), feature_set_id=params.feature_set_id)

    seed = (
        params.session_id
        if params.session_id is not None
        else results.deterministic_seed(prepared.dataset_digest)
    )
    training_config = {
        "task": task,
        "engine": persistence.ENGINE,
        "source": prepared.feature_set_id or prepared.dataset_digest,
        "seed": seed,
        **params.model_dump(exclude=_NOT_CONFIGURATION),
    }
    comparison = results.comparison_key(task, prepared.source_label, training_config)
    experiment_name = params.experiment_name or comparison
    failure = {"feature_set_id": prepared.feature_set_id, "comparison_key": comparison}

    # ── Replay: a recorded comparison is read, not retrained ─────────────
    recorded = await domain_api.get_model_comparison(comparison)
    if recorded:
        return json.dumps(
            results.recorded_response(
                recorded,
                experiment_name=experiment_name,
                prepared=prepared,
                reused=True,
                chart_data=chart_data_from_recorded(recorded),
            ),
            indent=2,
        )

    n_select = max(1, int(params.n_select))
    total_stages = 3 + n_select + 1  # load | compare | tune×N | blend + record
    emit("tool_log", f"Starting {task} experiment '{experiment_name}'...")
    emit_progress(current=1, total=total_stages, label="Loading data & building the preprocessing pipeline")

    data = pd.read_csv(prepared.train_path)
    if len(data) < MIN_ROWS:
        return error_result(
            "insufficient_data",
            f"The dataset has only {len(data)} rows, too few for cross-validated training; "
            f"at least {MIN_ROWS} are required.",
            rows=int(len(data)), cols=int(data.shape[1]), **failure,
        )
    if TARGET not in data.columns:
        return error_result("missing_target", f"The data has no '{TARGET}' column.", **failure)
    if data[TARGET].isna().any():
        return error_result(
            "missing_target",
            "The target column contains missing values. Every training row needs a target.",
            **failure,
        )
    if task == estimators.CLASSIFICATION:
        class_counts = data[TARGET].value_counts()
        if len(class_counts) < 2:
            return error_result(
                "insufficient_classes",
                "Classification requires at least two target classes.",
                class_distribution=results.json_safe(class_counts.to_dict()), **failure,
            )
    else:
        if not pd.api.types.is_numeric_dtype(data[TARGET]):
            return error_result("invalid_target", "Regression needs a numeric target column.", **failure)

    config = preprocessing_config(params)
    train_df, holdout_df, holdout_used, train_size = split_rows(
        task, data, prepared.holdout_path, seed=seed,
        stratify=params.data_split_stratify, shuffle=params.data_split_shuffle,
    )
    try:
        ds = dataset.build(task, train_df, holdout_df, config)
    except ValueError as exc:
        return error_result("invalid_input", str(exc), **failure)

    folds = cv.clamp_folds(task, params.fold, pd.Series(ds.y_train))
    if folds < 2:
        return error_result(
            "insufficient_data",
            "The rarest class has fewer than two rows in the training partition, so "
            "2-fold cross-validation is impossible.",
            class_distribution=results.json_safe(pd.Series(train_df[TARGET]).value_counts().to_dict()),
            fold=int(params.fold), **failure,
        )

    small_cohort = task == estimators.CLASSIFICATION and len(data) < SMALL_COHORT_ROWS
    if small_cohort:
        n_select = min(n_select, SMALL_COHORT_N_SELECT)
    try:
        candidates = estimators.select(
            task,
            include=csv_list(params.include_models),
            exclude=csv_list(params.exclude_models),
            small_cohort=small_cohort,
        )
    except ValueError as exc:
        return error_result("invalid_model_id", str(exc), **failure)
    if not candidates:
        return error_result("no_candidates", "No candidate model is left to compare.", **failure)

    warnings: list[str] = []
    if getattr(params, "use_gpu", False):
        warnings.append("use_gpu is not supported by the scikit-learn engine; trained on the CPU")
    balance = bool(getattr(params, "fix_imbalance", False)) and task == estimators.CLASSIFICATION
    n_jobs = settings.compute_cpu_threads()
    splitter = cv.splitter(
        task, folds, seed, shuffle=params.data_split_shuffle, stratify=params.data_split_stratify
    )

    def make_pipeline(entry: estimators.Estimator) -> Any:
        return build_pipeline(estimators.instantiate(entry, seed=seed, n_jobs=n_jobs), ds.columns, config)

    experiment = _Experiment(
        task=task,
        run_id=run_id,
        comparison=comparison,
        output_dir=os.path.join(work_root, results.slug(comparison)),
        data=ds,
        seed=seed,
        eval_kind=results.evaluation_kind(prepared, holdout_used),
        generalisation=results.generalisation_estimate(prepared, holdout_used),
        folds=folds,
        warnings=warnings,
    )
    logger.info(
        "experiment %s | comparison %s | %d train / %d evaluation rows | %d folds | %d candidates",
        experiment_name, comparison, len(train_df), len(holdout_df), folds, len(candidates),
    )

    # ── Compare ──────────────────────────────────────────────────────────
    emit_progress(current=2, total=total_stages, label=f"Comparing {len(candidates)} models")
    compared = await asyncio.to_thread(
        compare.compare, task, candidates, ds.X_train, ds.y_train,
        cv=splitter, make_pipeline=make_pipeline, balance=balance, n_classes=ds.n_classes,
    )
    for model_id, reason in compared.failures.items():
        warnings.append(f"{model_id}: excluded from the comparison ({reason})")
    if not compared.rows:
        return error_result(
            "no_model_trained",
            "No candidate model could be cross-validated; the dataset is likely too small.",
            failures=compared.failures, **failure,
        )

    # Every compared candidate is a variant: its CV row of the comparison table.
    variants: list[dict] = [
        {
            "variant_key": f"compare_{results.slug(row['model_id'])}",
            "algorithm": row["Model"],
            "parameters": results.versioned({"model_id": row["model_id"]}),
            "metrics": results.versioned({"stage": "comparison", "fold": folds, "cv": _cv_of(row)}),
            "selected": False,
            "status": "ready",
        }
        for row in compared.rows
    ]

    # ── Tune and evaluate the top models ─────────────────────────────────
    top = compared.top(n_select)
    tuned_models: list[tuple[str, Any]] = []  # (variant key, unfitted tuned pipeline)
    for index, row in enumerate(top):
        name = f"tuned_model_{index + 1}"
        entry = estimators.entry(task, row["model_id"])
        emit_progress(
            current=3 + index,
            total=total_stages,
            label=f"Tuning & evaluating {entry.name} ({index + 1}/{len(top)})",
        )
        try:
            tuned = await asyncio.to_thread(
                tune.tune, task, make_pipeline(entry), entry, ds.X_train, ds.y_train,
                cv=splitter, seed=seed, base_cv=_cv_of(row), balance=balance, n_classes=ds.n_classes,
            )
            fitted = await asyncio.to_thread(
                fitting.fit, tuned.model, ds.X_train, ds.y_train, balance=balance
            )
            parameters = {
                **results.estimator_spec(fitted),
                "model_id": entry.model_id,
                "tuning": {"improved": tuned.improved, "tried": tuned.tried, "params": tuned.params},
            }
            variants.append(await experiment.record_variant(
                name, entry.name, fitted, "tuned", tuned.cv, parameters, tree=entry.tree
            ))
            tuned_models.append((name, tuned.model))
        except Exception as exc:
            logger.error("could not tune %s: %s", name, exc, exc_info=True)
            variants.append({
                "variant_key": name,
                "algorithm": entry.name,
                "parameters": results.versioned({"model_id": entry.model_id}),
                "metrics": results.versioned({"stage": "tuned", "error": str(exc)[:500]}),
                "selected": False,
                "status": "failed",
            })

    # ── Blend the tuned models, then select the final one ────────────────
    emit_progress(
        current=total_stages,
        total=total_stages,
        label="Blending models & recording results" if len(tuned_models) > 1 else "Recording results",
    )
    final_name = None
    if len(tuned_models) > 1:
        try:
            blended = blend.blend(task, tuned_models)
            blend_cv = await asyncio.to_thread(
                fitting.cv_scores, task, blended, ds.X_train, ds.y_train, splitter,
                n_classes=ds.n_classes,
            )
            fitted_blend = await asyncio.to_thread(fitting.fit, blended, ds.X_train, ds.y_train)
            variants.append(await experiment.record_variant(
                "blended_model", "Blended", fitted_blend, "blended", blend_cv,
                {
                    **results.estimator_spec(fitted_blend),
                    "members": [name for name, _ in tuned_models],
                    "voting": getattr(fitted_blend, "voting", None),
                },
            ))
            final_name = "blended_model"
        except Exception as exc:
            logger.error("blending failed: %s", exc, exc_info=True)
            warnings.append(f"blending failed: {exc}")
    if final_name is None and tuned_models:
        final_name = tuned_models[0][0]
    if final_name is None:
        # Every tuning failed: the best compared candidate, untuned.
        best = compared.rows[0]
        entry = estimators.entry(task, best["model_id"])
        fitted = await asyncio.to_thread(
            fitting.fit, make_pipeline(entry), ds.X_train, ds.y_train, balance=balance
        )
        variants.append(await experiment.record_variant(
            "best_untuned", entry.name, fitted, "comparison_best", _cv_of(best),
            {**results.estimator_spec(fitted), "model_id": entry.model_id}, tree=entry.tree,
        ))
        final_name = "best_untuned"
    for variant in variants:
        if variant["variant_key"] == final_name and variant["status"] == "ready":
            variant["selected"] = True

    # ── Record the comparison (one transaction) ──────────────────────────
    preprocessing: dict[str, Any] = {"pipeline": pipeline_document(ds.columns, config)}
    if task == estimators.CLASSIFICATION:
        preprocessing.update(
            class_balance={
                "requested": bool(params.fix_imbalance),
                "effective": balance,
                "method": "balanced_sample_weight" if balance else None,
            },
            requested_fix_imbalance=bool(params.fix_imbalance),
            effective_fix_imbalance=balance,
        )
    comparison_payload = results.comparison_payload(
        prepared,
        preprocessing=preprocessing,
        features=ds.features,
        split_definition={
            "kind": experiment.eval_kind,
            "fold": folds,
            "train_rows": int(len(train_df)),
            "holdout_rows": int(len(holdout_df)) if holdout_used else None,
            "eval_rows": int(len(holdout_df)),
            "train_size": train_size,
            "stratify": params.data_split_stratify,
            "shuffle": params.data_split_shuffle,
        },
        seeds={"session_id": seed, "derived_from_dataset_digest": params.session_id is None},
        variants=variants,
    )
    try:
        recorded = await domain_api.record_model_comparison(comparison, comparison_payload)
    except domain_api.DomainApiError as exc:
        # No file fallback — an unrecorded comparison did not happen.
        return domain_api.error_result_from(exc, "the model results could not be recorded", **failure)

    emit("tool_log", f"{task.capitalize()} experiment...Done")
    return json.dumps(
        results.recorded_response(
            recorded,
            experiment_name=experiment_name,
            prepared=prepared,
            reused=False,
            figures=experiment.figures,
            warnings=warnings,
            chart_data=chart_data_from_recorded(recorded),
        ),
        indent=2,
    )


__all__ = ["csv_list", "preprocessing_config", "split_rows", "train"]
