"""Refitting a recorded variant — the recovery path."""

from __future__ import annotations

import importlib
from typing import Any, Mapping, Optional

# Setup parameters that are data, not configuration.
SETUP_EXCLUDED = ("data", "test_data", "experiment_name", "html", "verbose")


def task_of(comparison_key: str) -> str:
    task = str(comparison_key).split(":", 1)[0]
    if task not in ("classification", "regression"):
        raise ValueError(f"comparison {comparison_key!r} is not a PyCaret classification or regression")
    return task


def refit_refusal(result: Mapping[str, Any]) -> Optional[str]:
    """Why a recorded variant cannot be refitted from its record, or None."""
    parameters = result.get("parameters") or {}
    if parameters.get("members"):
        return (
            "a blended variant is refitted by refitting its member variants; its "
            "record names them in parameters.members"
        )
    if not parameters.get("estimator"):
        return (
            "the variant records no estimator — it is a compared candidate whose "
            "pipeline was never saved, so there is nothing to recover"
        )
    if (result.get("seeds") or {}).get("session_id") is None:
        return "the variant records no seed"
    if (result.get("split_definition") or {}).get("kind") == "independent_evaluation":
        return "an independent evaluation reuses its base variant's model; refit the base variant"
    return None


def build_estimator(spec: Mapping[str, Any]) -> Any:
    """The estimator a variant recorded, rebuilt from its class and parameters."""
    path = str(spec.get("estimator") or "")
    module_name, _, class_name = path.rpartition(".")
    if not module_name or not class_name:
        raise ValueError(f"cannot rebuild an estimator from {path!r}")
    estimator_class = getattr(importlib.import_module(module_name), class_name)
    return estimator_class(**dict(spec.get("params") or {}))


def setup_arguments(recorded_setup: Mapping[str, Any], seed: int) -> dict[str, Any]:
    arguments = {key: value for key, value in recorded_setup.items() if key not in SETUP_EXCLUDED}
    arguments["session_id"] = int(seed)
    return arguments


def fit_from_spec(
    *,
    task: str,
    train: Any,
    holdout: Any,
    setup_kwargs: Mapping[str, Any],
    spec: Mapping[str, Any],
):
    """Run PyCaret setup with the recorded configuration, fit the recorded
    estimator, and predict the evaluation rows. Returns (module, model,
    predictions)."""
    from .results import disjoint_indices

    module = importlib.import_module(f"pycaret.{task}")
    arguments = dict(setup_kwargs)
    if holdout is not None:
        # The recorded evaluation rows, exactly as the original run used them
        # (and indexed exactly as the training tools index them).
        train, holdout = disjoint_indices(train, holdout)
        arguments["test_data"] = holdout
        arguments.pop("train_size", None)
    arguments.update(data=train, html=False, verbose=False)
    module.setup(**arguments)
    model = module.create_model(build_estimator(spec), verbose=False)
    try:
        predictions = module.predict_model(model, raw_score=True, verbose=False)
    except TypeError:
        predictions = module.predict_model(model, verbose=False)
    return module, model, predictions


def prediction_agreement(recorded: Any, refitted: Any) -> dict[str, Any]:
    """How closely the refit reproduces the recorded per-case predictions."""
    column = next(
        (name for name in ("prediction_label", "Label", "predicted_y") if name in refitted.columns),
        None,
    )
    if column is None or recorded is None or column not in recorded.columns:
        return {"compared": False, "reason": "no comparable prediction column"}
    if len(recorded) != len(refitted):
        return {"compared": False, "reason": "the evaluation sets differ in size"}
    left = recorded[column].to_numpy()
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
    "fit_from_spec",
    "prediction_agreement",
    "refit_refusal",
    "setup_arguments",
    "task_of",
]
