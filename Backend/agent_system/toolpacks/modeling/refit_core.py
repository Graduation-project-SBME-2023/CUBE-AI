"""Refitting a recorded variant — the recovery path.

A variant is rebuilt from its record alone: the estimator from
``parameters.estimator`` (a scikit-learn, LightGBM or XGBoost class path), the
preprocessing from ``preprocessing``, the split from ``split_definition`` and
the seed from ``seeds``. Both kinds of record are accepted:

- an engine record (``environment.engine == "sklearn"``) keeps its pipeline
  settings in ``preprocessing.pipeline.config``;
- a record trained with PyCaret keeps its setup arguments in
  ``preprocessing.setup``; the arguments the engine knows (``normalize``,
  ``transformation``, ``pca``, the feature lists, ...) are mapped onto the
  engine pipeline and SMOTE becomes balanced sample weights, so its numbers
  can differ slightly from the original fit (merged plan, Phase 7.2).
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any, Optional

import pandas as pd

from .engine import cv, dataset, estimators, fitting, persistence
from .engine.preprocessing import TARGET, PreprocessingConfig, build_pipeline, pipeline_document


def task_of(comparison_key: str) -> str:
    task = str(comparison_key).split(":", 1)[0]
    if task not in estimators.TASKS:
        raise ValueError(f"comparison {comparison_key!r} is not a classification or regression")
    return task


def refit_refusal(result: Mapping[str, Any]) -> Optional[str]:
    """Why a recorded variant cannot be refitted from its record, or None."""
    parameters = result.get("parameters") or {}
    if parameters.get("members"):
        return (
            "a blended variant is refitted by refitting its member variants; its "
            "record names them in parameters.members"
        )
    estimator = str(parameters.get("estimator") or "")
    if not estimator:
        return (
            "the variant records no estimator — it is a compared candidate whose "
            "pipeline was never saved, so there is nothing to recover"
        )
    if estimator.startswith("pycaret."):
        return (
            f"the variant's estimator is a PyCaret class ({estimator}), which cannot be "
            "rebuilt without PyCaret; train a new comparison instead"
        )
    if (result.get("seeds") or {}).get("session_id") is None:
        return "the variant records no seed"
    if (result.get("split_definition") or {}).get("kind") == "independent_evaluation":
        return "an independent evaluation reuses its base variant's model; refit the base variant"
    return None


def build_estimator(spec: Mapping[str, Any], *, n_jobs: Optional[int] = None) -> Any:
    """The estimator a variant recorded, rebuilt from its class and parameters;
    ``n_jobs`` replaces the recorded thread count when the class takes one."""
    estimator_class = estimators.load_class(str(spec.get("estimator") or ""))
    params = dict(spec.get("params") or {})
    if n_jobs is not None and "n_jobs" in inspect.signature(estimator_class).parameters:
        params["n_jobs"] = int(n_jobs)
    return estimator_class(**params)


def preprocessing_config(result: Mapping[str, Any]) -> tuple[PreprocessingConfig, str]:
    """The recorded preprocessing as engine settings, and where it came from
    (``"engine"`` or ``"pycaret_setup"``)."""
    preprocessing = result.get("preprocessing") or {}
    pipeline = preprocessing.get("pipeline") or {}
    if pipeline.get("config") is not None:
        return PreprocessingConfig.from_mapping(pipeline["config"]), "engine"
    return PreprocessingConfig.from_mapping(preprocessing.get("setup") or {}), "pycaret_setup"


def class_balance(result: Mapping[str, Any]) -> bool:
    preprocessing = result.get("preprocessing") or {}
    balance = preprocessing.get("class_balance") or {}
    if "effective" in balance:
        return bool(balance["effective"])
    return bool(preprocessing.get("effective_fix_imbalance", False))


def refit_preprocessing(result: Mapping[str, Any], data: dataset.Dataset) -> dict[str, Any]:
    """The preprocessing document of the refitted variant: the record's own
    for an engine record, the mapped engine pipeline for a PyCaret record."""
    config, source = preprocessing_config(result)
    if source == "engine":
        return dict(result.get("preprocessing") or {})
    recorded = result.get("preprocessing") or {}
    balance = class_balance(result)
    document: dict[str, Any] = {
        "pipeline": pipeline_document(data.columns, config),
        "mapped_from_pycaret_setup": recorded.get("setup") or {},
    }
    if data.task == estimators.CLASSIFICATION:
        document["class_balance"] = {
            "requested": bool(recorded.get("requested_fix_imbalance", balance)),
            "effective": balance,
            "method": "balanced_sample_weight" if balance else None,
        }
    return document


def evaluation_rows(
    task: str, train: pd.DataFrame, holdout: Optional[pd.DataFrame], result: Mapping[str, Any]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The recorded evaluation rows when there were withheld rows; otherwise
    the engine's seeded random split (which reproduces an engine record's
    split, but not PyCaret's)."""
    if holdout is not None:
        return train.reset_index(drop=True), holdout.reset_index(drop=True)
    split = result.get("split_definition") or {}
    fitted, evaluated, _ = cv.random_split(
        task,
        train,
        target=TARGET,
        seed=int(result["seeds"]["session_id"]),
        stratify=bool(split.get("stratify", True)),
        shuffle=bool(split.get("shuffle", True)),
    )
    return fitted.reset_index(drop=True), evaluated.reset_index(drop=True)


def fit_from_record(
    *,
    task: str,
    train: pd.DataFrame,
    holdout: Optional[pd.DataFrame],
    result: Mapping[str, Any],
    n_jobs: Optional[int] = None,
) -> tuple[Any, dataset.Dataset, pd.DataFrame]:
    """Fit the recorded estimator with the recorded preprocessing and predict
    the evaluation rows. Returns (fitted pipeline, dataset, predictions)."""
    config, _ = preprocessing_config(result)
    fitted_rows, evaluated_rows = evaluation_rows(task, train, holdout, result)
    data = dataset.build(task, fitted_rows, evaluated_rows, config)
    model = build_pipeline(build_estimator(result["parameters"], n_jobs=n_jobs), data.columns, config)
    fitting.fit(model, data.X_train, data.y_train, balance=class_balance(result) and task == estimators.CLASSIFICATION)
    predictions = dataset.predictions_frame(task, model, data.eval_frame, data.features, data.classes)
    return model, data, predictions


def save_refit(model: Any, data: dataset.Dataset, path: str) -> str:
    persistence.save_bundle(persistence.make_bundle(data.task, model, data.features, data.classes), path)
    return path


def prediction_agreement(recorded: Any, refitted: Any) -> dict[str, Any]:
    """How closely the refit reproduces the recorded per-case predictions."""
    column = next(
        (name for name in ("prediction_label", "Label", "predicted_y") if name in refitted.columns),
        None,
    )
    if column is None or recorded is None:
        return {"compared": False, "reason": "no comparable prediction column"}
    recorded_column = next(
        (name for name in (column, "prediction_label", "Label", "predicted_y") if name in recorded.columns),
        None,
    )
    if recorded_column is None:
        return {"compared": False, "reason": "no comparable prediction column"}
    if len(recorded) != len(refitted):
        return {"compared": False, "reason": "the evaluation sets differ in size"}
    left = recorded[recorded_column].to_numpy()
    right = refitted[column].to_numpy()
    try:
        difference = float(abs(left.astype(float) - right.astype(float)).max())
        exact = difference == 0.0
    except (TypeError, ValueError):
        difference = None
        exact = bool((left == right).all())
    return {
        "compared": True,
        "cases": int(len(refitted)),
        "identical_predictions": exact,
        "label_agreement": float((left == right).mean()),
        "max_abs_difference": difference,
    }


__all__ = [
    "build_estimator",
    "class_balance",
    "evaluation_rows",
    "fit_from_record",
    "prediction_agreement",
    "preprocessing_config",
    "refit_preprocessing",
    "refit_refusal",
    "save_refit",
    "task_of",
]
