"""refit_model — rebuild a recorded model on the scikit-learn engine.

Used when a fitted pipeline was lost or corrupted, and for a model trained
with PyCaret, whose saved file cannot be loaded any more (the inference tools
answer ``legacy_model_format``). The refit is written as a NEW variant of the
same comparison, with ``environment.refit_of`` naming the original, because
its numbers can differ slightly from the original fit.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import uuid
from typing import Any

import pandas as pd

from .._toolkit import error_result, tool
from ..common.manifests import INTERIM_HEAVY
from ..radiomics.digests import bytes_digest, document_digest
from ..common import domain_api, settings
from ..common.workspace import get_workspace
from . import evaluation, refit_core, results
from .engine import estimators
from .engine.preprocessing import TARGET


async def _training_data(result: dict) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Re-materialize exactly what the variant trained on, or refuse."""
    split = result.get("split_definition") or {}
    if result.get("feature_set_id"):
        feature_set_id = str(result["feature_set_id"])
        everything = await domain_api.export_feature_set(feature_set_id, part="all", fmt="csv")
        if bytes_digest(everything.encode("utf-8")) != result["dataset_digest"]:
            raise ValueError(
                "the frozen feature set no longer exports the dataset this variant was "
                "trained on (dataset_digest differs); refusing to refit on other data"
            )
        if split.get("kind") == "selection_holdout":
            train = await domain_api.export_feature_set(feature_set_id, part="train", fmt="csv")
            holdout = await domain_api.export_feature_set(feature_set_id, part="holdout", fmt="csv")
            return pd.read_csv(io.StringIO(train)), pd.read_csv(io.StringIO(holdout))
        return pd.read_csv(io.StringIO(everything)), None

    payload = await domain_api.artifact_content(str(result["input_file_id"]))
    holdout_payload = None
    if split.get("holdout_artifact_id"):
        holdout_payload = await domain_api.artifact_content(str(split["holdout_artifact_id"]))
        digest = document_digest(
            {"input": bytes_digest(payload), "holdout": bytes_digest(holdout_payload)}
        )
    else:
        digest = bytes_digest(payload)
    if digest != result["dataset_digest"]:
        raise ValueError(
            "the captured input no longer matches the recorded dataset_digest; refusing to refit"
        )
    holdout = pd.read_csv(io.BytesIO(holdout_payload)) if holdout_payload is not None else None
    return pd.read_csv(io.BytesIO(payload)), holdout


@tool(
    name="refit_model",
    visibility={"agent"},
    **INTERIM_HEAVY,
)
async def refit_model(model_result_id: str) -> str | dict[str, Any]:
    """Refit a recorded model on the scikit-learn engine: a lost or corrupted
    pipeline, or a model trained with PyCaret (which can no longer be loaded)."""
    run_id = domain_api.require_run()
    try:
        result = await domain_api.request("GET", f"/model-results/{model_result_id}")
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, "the model result could not be read")
    refusal = refit_core.refit_refusal(result)
    if refusal:
        return error_result("not_refittable", f"Cannot refit: {refusal}.", model_result_id=model_result_id)
    task = refit_core.task_of(result["comparison_key"])
    try:
        train, holdout = await _training_data(result)
    except ValueError as exc:
        return error_result("input_changed", f"Cannot refit: {exc}.", model_result_id=model_result_id)
    try:
        model, data, predictions = await asyncio.to_thread(
            refit_core.fit_from_record,
            task=task,
            train=train,
            holdout=holdout,
            result=result,
            n_jobs=settings.compute_cpu_threads(),
        )
    except (ImportError, ValueError) as exc:
        return error_result("not_refittable", f"Cannot refit: {exc}.", model_result_id=model_result_id)

    # The new file's digest names the new variant, so a second refit is a
    # second variant rather than a conflicting rewrite. The bundle is written
    # beside its final name and renamed into place.
    comparison = result["comparison_key"]
    pending = results.model_file_base(get_workspace(), comparison, f".refit_{uuid.uuid4().hex}") + ".pkl"
    await asyncio.to_thread(refit_core.save_refit, model, data, pending)
    digest = results.file_digest(pending)
    variant = f"{result['variant_key']}__refit_{digest[:12]}"
    base = results.model_file_base(get_workspace(), comparison, variant)
    os.replace(pending, base + ".pkl")

    if task == estimators.CLASSIFICATION:
        scored = evaluation.holdout_metrics(predictions, predictions[TARGET])
        refit_metrics = scored["metrics"]
    else:
        refit_metrics = evaluation.regression_metrics(predictions, TARGET)

    recorded_predictions = None
    if result.get("predictions_file_id"):
        recorded_predictions = pd.read_csv(
            io.BytesIO(await domain_api.artifact_content(str(result["predictions_file_id"])))
        )
    agreement = refit_core.prediction_agreement(recorded_predictions, predictions)

    artifact = await results.publish_predictions(
        predictions,
        run_id=run_id,
        comparison=comparison,
        variant=variant,
        metadata={"refit_of": str(result["model_result_id"])},
    )
    recorded_evaluation = (result.get("metrics") or {}).get("evaluation") or {}
    try:
        await domain_api.record_model_comparison(
            comparison,
            {
                "cohort_id": str(result["cohort_id"]),
                "feature_set_id": result.get("feature_set_id"),
                "input_file_id": result.get("input_file_id"),
                "dataset_digest": result["dataset_digest"],
                "preprocessing": results.versioned(refit_core.refit_preprocessing(result, data)),
                "feature_order": results.versioned({"features": data.features, "target": TARGET}),
                "split_definition": result["split_definition"],
                "seeds": result["seeds"],
                "environment": results.versioned({
                    **results.environment_document(),
                    "refit_of": str(result["model_result_id"]),
                }),
                "variants": [{
                    "variant_key": variant,
                    "algorithm": result["algorithm"],
                    "parameters": result["parameters"],
                    "metrics": results.versioned({
                        "stage": "refit",
                        "refit_of": str(result["model_result_id"]),
                        "agreement": agreement,
                        "evaluation": {
                            "kind": recorded_evaluation.get("kind"),
                            "n_cases": int(len(predictions)),
                            "generalisation_estimate": bool(
                                recorded_evaluation.get("generalisation_estimate")
                            ),
                            "metrics": results.json_safe(refit_metrics or {}),
                        },
                    }),
                    "predictions_file_id": str(artifact["file_id"]),
                    "model_file_name": results.model_file_logical_name(comparison, variant),
                    "model_file_digest": digest,
                    "selected": False,
                    "status": "ready",
                }],
            },
        )
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, "the refitted variant could not be recorded")
    return json.dumps({
        "status": "success",
        "refit_of": str(result["model_result_id"]),
        "comparison_key": comparison,
        "variant_key": variant,
        "model_path": base,
        "model_file_digest": digest,
        "agreement": agreement,
        "recorded_metrics": recorded_evaluation.get("metrics"),
        "refit_metrics": results.json_safe(refit_metrics),
    }, indent=2)
