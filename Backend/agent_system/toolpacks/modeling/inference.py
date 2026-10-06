"""Predicting with a recorded model — the body of ``run_classifier_inference``
and ``run_regression_inference``.

The model file is identified by the digest recorded for it and never trusted
otherwise. A model trained with PyCaret (a record without
``environment.engine == "sklearn"``) cannot be loaded any more: the tools
return ``legacy_model_format`` and name ``refit_model``, which rebuilds it on
the engine from its record.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
from typing import Any, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel

from .._toolkit import error_result
from ..common import domain_api
from ..radiomics.digests import bytes_digest
from . import results
from .engine import dataset, estimators, persistence
from .evaluation import regression_metrics
from storage_paths import TEMP_DIR

logger = logging.getLogger(__name__)

TOOL_FOR_TASK = {
    estimators.CLASSIFICATION: "run_classifier_inference",
    estimators.REGRESSION: "run_regression_inference",
}


def resolve_model_path(model_path: str) -> str:
    """``model_path`` without ``.pkl``; a unique prefix match in its directory
    when the exact file does not exist."""
    path = model_path.removesuffix(".pkl")
    directory = os.path.dirname(path)
    if not os.path.exists(path + ".pkl") and os.path.isdir(directory):
        base = os.path.basename(path)
        candidates = sorted(f for f in os.listdir(directory) if f.startswith(base) and f.endswith(".pkl"))
        if candidates:
            path = os.path.join(directory, candidates[0]).removesuffix(".pkl")
    return path


def legacy_model_error(model_path: str, variant: Optional[dict[str, Any]], reason: str) -> dict[str, Any]:
    model_result_id = str(variant["model_result_id"]) if variant and variant.get("model_result_id") else None
    how = (
        f"Call refit_model with model_result_id='{model_result_id}' to rebuild it on the "
        "scikit-learn engine, then run inference on the refitted model."
        if model_result_id
        else "Refit it with refit_model from its recorded model result, then run inference on the refitted model."
    )
    return error_result(
        "legacy_model_format",
        f"This model was not trained by the scikit-learn engine ({reason}), so it cannot be "
        f"loaded. {how}",
        model_path=model_path,
        model_result_id=model_result_id,
    )


def classification_metrics(y_true: pd.Series, predictions: pd.DataFrame) -> dict[str, Any]:
    """Accuracy, the confusion matrix and — binary — AUC, precision, recall,
    sensitivity, specificity, F1 and the four counts; macro scores otherwise."""
    from sklearn.metrics import (
        accuracy_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    y_pred = predictions["prediction_label"]
    observed = dataset.sorted_labels(list(y_true))
    metrics: dict[str, Any] = {"accuracy": float(accuracy_score(y_true, y_pred))}
    matrix = confusion_matrix(y_true, y_pred, labels=observed)
    metrics["confusion_matrix"] = matrix.tolist()
    metrics["confusion_matrix_labels"] = [str(label) for label in observed]
    if len(observed) == 2:
        positive = observed[1]
        column = dataset.score_column(predictions, positive)
        if column is not None:
            try:
                metrics["auc"] = float(roc_auc_score(y_true == positive, predictions[column]))
            except ValueError:
                pass
        tn, fp, fn, tp = matrix.ravel()
        kwargs = {"pos_label": positive, "zero_division": 0}
        recall = float(recall_score(y_true, y_pred, **kwargs))
        metrics.update({
            "precision": float(precision_score(y_true, y_pred, **kwargs)),
            "recall": recall,
            "sensitivity": recall,
            "specificity": float(tn / (tn + fp)) if (tn + fp) > 0 else 0.0,
            "f1": float(f1_score(y_true, y_pred, **kwargs)),
            "true_positives": int(tp),
            "false_positives": int(fp),
            "true_negatives": int(tn),
            "false_negatives": int(fn),
        })
    else:
        kwargs = {"average": "macro", "zero_division": 0}
        metrics.update({
            "precision_macro": float(precision_score(y_true, y_pred, **kwargs)),
            "recall_macro": float(recall_score(y_true, y_pred, **kwargs)),
            "f1_macro": float(f1_score(y_true, y_pred, **kwargs)),
        })
    return metrics


async def run_inference(task: str, params: BaseModel) -> str | dict[str, Any]:
    run_id = domain_api.require_run()
    os.makedirs(TEMP_DIR / "modeling" / run_id / "inference", exist_ok=True)
    logger.info("%s inference | model %s | input %s", task, params.model_path, params.input_path)
    failure = {"input_path": params.input_path, "model_path": params.model_path}

    try:
        with open(params.input_path, "rb") as stream:
            input_bytes = stream.read()
        data = pd.read_csv(io.BytesIO(input_bytes))
    except (OSError, ValueError) as exc:
        return error_result("invalid_input", f"The input CSV could not be read: {exc}", **failure)

    ground_truth = params.ground_truth_column
    has_ground_truth = ground_truth is not None and ground_truth in data.columns
    if ground_truth and not has_ground_truth:
        logger.warning("column %r not found; metrics skipped", ground_truth)

    model_path = resolve_model_path(params.model_path)
    try:
        base_variant = await results.recorded_variant_for(model_path)
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, "the recorded model results could not be read", **failure)
    if base_variant is not None and not persistence.is_engine_record(base_variant):
        return legacy_model_error(params.model_path, base_variant, "it was trained with PyCaret")
    mismatch = results.model_file_mismatch(model_path, base_variant)
    if mismatch:
        return error_result("model_file_mismatch", mismatch, **failure)
    if not os.path.exists(model_path + ".pkl"):
        return error_result("model_not_found", f"Model file not found: {model_path}.pkl", **failure)
    try:
        bundle = await asyncio.to_thread(persistence.load_bundle, model_path + ".pkl")
    except persistence.LegacyModelError as exc:
        return legacy_model_error(params.model_path, base_variant, str(exc))
    if bundle.get("task") != task:
        return error_result(
            "wrong_task",
            f"This is a {bundle.get('task')} model; use {TOOL_FOR_TASK.get(bundle.get('task'), 'the matching tool')}.",
            **failure,
        )
    missing = dataset.missing_features(data, bundle["features"])
    if missing:
        return error_result(
            "missing_features",
            f"The input lacks {len(missing)} feature column(s) the model was trained on: "
            f"{', '.join(missing[:20])}",
            missing_features=missing, **failure,
        )

    try:
        predictions = await asyncio.to_thread(
            dataset.predictions_frame, task, bundle["pipeline"], data, bundle["features"],
            dataset.bundle_classes(bundle),
        )
    except Exception as exc:
        logger.error("prediction failed: %s", exc, exc_info=True)
        return error_result("prediction_failed", f"Failed to make predictions: {exc}", **failure)

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

    metrics: Optional[dict[str, Any]] = None
    if has_ground_truth:
        if task == estimators.CLASSIFICATION:
            metrics = classification_metrics(data[ground_truth].reset_index(drop=True), predictions)
        else:
            scored = predictions.copy()
            scored["target"] = np.asarray(data[ground_truth], dtype=float)
            metrics = regression_metrics(scored, "target")

    recorded_evaluation = None
    warnings: list[str] = []
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
        "metrics": results.json_safe(metrics) if metrics else None,
        "recorded_evaluation": recorded_evaluation,
        "warnings": warnings,
        "input_path": params.input_path,
        "model_path": params.model_path,
    }, indent=2)


__all__ = ["classification_metrics", "legacy_model_error", "resolve_model_path", "run_inference"]
