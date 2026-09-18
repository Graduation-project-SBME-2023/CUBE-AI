"""pycaret_refit_model — recover a lost fitted pipeline."""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import tempfile

import pandas as pd

from .. import mcp
from ..feature_extraction.digests import bytes_digest, document_digest
from ..helpers import domain_api
from ..helpers.workspace import get_workspace
from . import evaluation, refit_core, results
from storage_paths import TEMP_DIR


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

    payload = await domain_api.artifact_content(str(result["input_artifact_id"]))
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


@mcp.tool(
    name="pycaret_refit_model",
    annotations={
        "title": "Refit a Recorded Model From Its Record",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def pycaret_refit_model(model_result_id: str) -> str:
    """Refit a recorded model whose fitted pipeline was lost or corrupted."""
    try:
        run_id = domain_api.require_run()
        result = await domain_api.request("GET", f"/model-results/{model_result_id}")
        refusal = refit_core.refit_refusal(result)
        if refusal:
            return json.dumps({"status": "error", "error_message": f"Cannot refit: {refusal}."}, indent=2)
        task = refit_core.task_of(result["comparison_key"])
        train, holdout = await _training_data(result)
        setup = refit_core.setup_arguments(
            (result.get("preprocessing") or {}).get("setup") or {},
            result["seeds"]["session_id"],
        )
        module, model, predictions = await asyncio.to_thread(
            refit_core.fit_from_spec,
            task=task,
            train=train,
            holdout=holdout,
            setup_kwargs=setup,
            spec=result["parameters"],
        )

        # The new file's digest names the new variant, so a second refit is
        # a second variant rather than a conflicting rewrite.
        scratch = tempfile.mkdtemp(dir=TEMP_DIR)
        try:
            module.save_model(model, os.path.join(scratch, "refit"))
            digest = results.file_digest(os.path.join(scratch, "refit.pkl"))
            variant = f"{result['variant_key']}__refit_{digest[:12]}"
            base = results.model_file_base(get_workspace(), result["comparison_key"], variant)
            shutil.move(os.path.join(scratch, "refit.pkl"), base + ".pkl")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

        if task == "classification":
            scored = (
                evaluation.holdout_metrics(predictions, predictions["target"])
                if "target" in predictions.columns
                else None
            )
            refit_metrics = scored["metrics"] if scored else None
        else:
            refit_metrics = evaluation.regression_metrics(predictions)

        recorded_predictions = None
        if result.get("predictions_artifact_id"):
            recorded_predictions = pd.read_csv(
                io.BytesIO(await domain_api.artifact_content(str(result["predictions_artifact_id"])))
            )
        agreement = refit_core.prediction_agreement(recorded_predictions, predictions)

        artifact = await results.publish_predictions(
            predictions,
            run_id=run_id,
            comparison=result["comparison_key"],
            variant=variant,
            metadata={"refit_of": str(result["model_result_id"])},
        )
        recorded_evaluation = (result.get("metrics") or {}).get("evaluation") or {}
        await domain_api.record_model_comparison(
            result["comparison_key"],
            {
                "cohort_id": str(result["cohort_id"]),
                "feature_set_id": result.get("feature_set_id"),
                "input_artifact_id": result.get("input_artifact_id"),
                "dataset_digest": result["dataset_digest"],
                "preprocessing": result["preprocessing"],
                "feature_order": result["feature_order"],
                "split_definition": result["split_definition"],
                "seeds": result["seeds"],
                "environment": results.environment_document(),
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
                    "predictions_artifact_id": str(artifact["artifact_id"]),
                    "model_file_name": results.model_file_logical_name(result["comparison_key"], variant),
                    "model_file_digest": digest,
                    "selected": False,
                    "status": "ready",
                }],
            },
        )
        return json.dumps({
            "status": "success",
            "refit_of": str(result["model_result_id"]),
            "comparison_key": result["comparison_key"],
            "variant_key": variant,
            "model_path": base,
            "model_file_digest": digest,
            "agreement": agreement,
            "recorded_metrics": recorded_evaluation.get("metrics"),
            "refit_metrics": results.json_safe(refit_metrics),
        }, indent=2)
    except Exception as exc:
        return json.dumps({"status": "error", "error_message": str(exc)}, indent=2)
