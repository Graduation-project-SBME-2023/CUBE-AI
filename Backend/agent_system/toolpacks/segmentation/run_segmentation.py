"""``run_segmentation``: segment one series with a MONAI Label model and save
the mask to the MONAI Label datastore (as DICOM-SEG).

The MONAI Label calls are in ``monai_client``; this module builds the label
metadata from the requested organs and turns the outcome into the tool's
result. A mask that was computed but not saved is a failure.
"""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from pydantic import Field

from .._toolkit import error_result, tool
from ..common.manifests import INTERIM_HEAVY
from ..common.service_urls import inference_api_url
from . import monai_client
from .monai_client import InferenceError

logger = logging.getLogger(__name__)

DEFAULT_COLOR = [255, 0, 0]
ORGAN_COLORS = {
    "spleen": [255, 0, 0],
    "liver": [139, 69, 19],
    "kidney": [255, 255, 0],
    "pancreas": [255, 165, 0],
    "lung": [173, 216, 230],
    "heart": [255, 192, 203],
}
# Inference itself, plus the result download and the datastore upload.
TOOL_TIMEOUT_S = monai_client.DEFAULT_JOB_TIMEOUT_S + 900


def build_label_info(organs: list[str], model: str) -> list[dict[str, Any]]:
    """The label metadata MONAI Label and the datastore expect, one entry per organ."""
    return [
        {
            "name": organ,
            "idx": index,
            "color": ORGAN_COLORS.get(organ, DEFAULT_COLOR),
            "description": f"{organ.capitalize()} segmentation",
            "model_name": model,
        }
        for index, organ in enumerate(organs, start=1)
    ]


def _failure(exc: InferenceError, started: float) -> dict[str, Any]:
    code = {
        "inference_timeout": "timeout",
        "parse_response": "invalid_response",
        "datastore_save": "datastore_save_failed",
    }.get(exc.stage, "inference_failed")
    return error_result(
        code,
        str(exc),
        stage=exc.stage,
        elapsed_seconds=round(time.monotonic() - started, 1),
        **exc.details,
    )


@tool(
    visibility={"agent"},
    timeout_s=TOOL_TIMEOUT_S,
    resources={"monai_inference"},
    **INTERIM_HEAVY,
)
async def run_segmentation(
    model: Annotated[str, Field(description="MONAI Label model name to run inference with")],
    series_instance_uid: Annotated[str, Field(description="DICOM SeriesInstanceUID to segment")],
    label_info: Annotated[
        str,
        Field(description="Comma-separated anatomical labels to segment (e.g. 'spleen' or 'spleen,liver,kidney')"),
    ],
    params: Annotated[dict[str, Any] | None, Field(description="Optional inference parameters")] = None,
) -> dict[str, Any]:
    """Run MONAI Label segmentation of one series with one model and save the
    mask to the MONAI Label datastore as DICOM-SEG. Returns the inference
    metadata and the datastore's label_id."""
    started = time.monotonic()
    clean_model = model.strip().rstrip(",")
    organs = [organ.strip().lower() for organ in label_info.split(",") if organ.strip()]
    if not organs:
        return error_result("invalid_args", "label_info must name at least one organ", stage="validation")

    labels = build_label_info(organs, clean_model)
    inference_params = {**(params or {}), "label_info": labels}
    try:
        inference = await monai_client.run_inference(clean_model, series_instance_uid, inference_params)
        saved_filename = f"seg_{series_instance_uid}_{clean_model}.nii.gz"
        upload_started = time.monotonic()
        label_id = await monai_client.save_label(
            series_instance_uid,
            saved_filename,
            inference.segmentation,
            {
                "label_info": labels,
                **({"latencies": inference.returned_params["latencies"]}
                   if "latencies" in inference.returned_params else {}),
            },
        )
    except InferenceError as exc:
        logger.warning("run_segmentation %s on %s failed at %s: %s", clean_model, series_instance_uid, exc.stage, exc)
        return _failure(exc, started)

    return {
        "endpoint": f"{inference_api_url()}/infer/{clean_model}",
        "query_params": {"image": series_instance_uid},
        "sent_params": inference_params,
        "status_code": 200,
        "inference_duration_seconds": round(inference.duration_s, 3),
        "inference_job_id": inference.job.get("job_id"),
        "retry_count": inference.job.get("attempt", 1),
        "error": False,
        "returned_params": inference.returned_params,
        "original_filename": inference.filename,
        "segmentation_bytes_count": len(inference.segmentation),
        "message": "Segmentation completed successfully",
        "saved": True,
        "saved_filename": saved_filename,
        "label_id": label_id,
        "upload_duration_seconds": round(time.monotonic() - upload_started, 3),
        "total_duration_seconds": round(time.monotonic() - started, 3),
    }
