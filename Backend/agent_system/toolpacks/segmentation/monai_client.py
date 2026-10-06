"""MONAI Label, as ``run_segmentation`` uses it: an inference job, its
multipart result, and the label upload to the datastore.

Everything goes through the shared HTTP client. A failure raises
``InferenceError`` with the stage it happened in; a 5xx body is never put in
the message (it is the server's traceback).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from ..common.http_client import shared_client
from ..common.service_urls import inference_api_url
from ..common.settings import positive_float

logger = logging.getLogger(__name__)

DEFAULT_JOB_TIMEOUT_S = 1800.0
DEFAULT_POLL_S = 3.0
UNAVAILABLE_GRACE_S = 600.0  # how long the status endpoint may be unreachable
START_TIMEOUT = httpx.Timeout(30.0)
STATUS_TIMEOUT = httpx.Timeout(10.0)
RESULT_TIMEOUT = httpx.Timeout(180.0, connect=15.0)
UPLOAD_TIMEOUT = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)
_FAILED_STATUSES = {"FAILED", "TIMED_OUT", "CANCELLED"}
_TRANSIENT = (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException, httpx.RemoteProtocolError)


class InferenceError(RuntimeError):
    """A MONAI Label call failed; ``stage`` names the step."""

    def __init__(self, stage: str, message: str, **details: Any):
        super().__init__(message)
        self.stage = stage
        self.details = details


@dataclass(frozen=True)
class InferenceResult:
    job: dict[str, Any]
    returned_params: dict[str, Any]
    segmentation: bytes
    filename: str
    duration_s: float


# Read when used and validated (L8): a bad value falls back to the default
# instead of failing the import of every tool in the pack.
def job_timeout_s() -> float:
    return positive_float("SEGMENTATION_JOB_TIMEOUT_SECONDS", DEFAULT_JOB_TIMEOUT_S)


def poll_s() -> float:
    return positive_float("SEGMENTATION_POLL_SECONDS", DEFAULT_POLL_S)


def inference_job_id(model: str, image: str, params: dict[str, Any]) -> str:
    """Deterministic: the same request names the same job, so a replay joins it."""
    payload = json.dumps(
        {"model": model, "image": image, "params": params}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _http_failure(stage: str, response: httpx.Response) -> InferenceError:
    detail = "" if response.status_code >= 500 else f": {response.text[:500]}"
    return InferenceError(
        stage, f"MONAI Label answered {response.status_code}{detail}", status_code=response.status_code
    )


async def _cancel_job(job_url: str) -> None:
    try:
        await asyncio.wait_for(shared_client().delete(job_url, timeout=STATUS_TIMEOUT), timeout=10.0)
    except Exception:  # best effort: the job's own max runtime ends it anyway
        logger.warning("could not cancel inference job %s", job_url)


async def _job_status(job_url: str) -> dict[str, Any] | None:
    """The job's status document, or ``None`` while the service is unavailable."""
    try:
        response = await shared_client().get(job_url, timeout=STATUS_TIMEOUT)
    except _TRANSIENT:
        return None
    if response.status_code >= 500 or response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise _http_failure("inference_status", response)
    return response.json()


async def _await_job(job_url: str, timeout_s: float) -> dict[str, Any]:
    """Poll the job until it succeeds; raise on failure, timeout or a status
    endpoint unavailable for longer than ``UNAVAILABLE_GRACE_S``."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    unavailable_since: float | None = None
    while True:
        if loop.time() - started >= timeout_s:
            await _cancel_job(job_url)
            raise InferenceError("inference_timeout", f"Inference exceeded {timeout_s:.0f}s")
        job = await _job_status(job_url)
        if job is None:
            now = loop.time()
            unavailable_since = unavailable_since or now
            if now - unavailable_since >= UNAVAILABLE_GRACE_S:
                raise InferenceError(
                    "inference_request",
                    f"Inference service remained unavailable for {UNAVAILABLE_GRACE_S:.0f}s",
                )
        else:
            unavailable_since = None
            status = str(job.get("status") or "").upper()
            if status == "SUCCEEDED":
                return job
            if status in _FAILED_STATUSES:
                raise InferenceError(
                    "inference_request", str(job.get("error") or f"Inference job {status.lower()}")
                )
        await asyncio.sleep(poll_s())


def _parse_result(response: httpx.Response) -> tuple[dict[str, Any], bytes, str]:
    """The multipart result: its JSON parameters, the mask bytes and the mask's file name."""
    from requests_toolbelt.multipart import decoder

    content_type = response.headers.get("content-type", "")
    if "multipart" not in content_type.lower():
        raise InferenceError("parse_response", f"The result is not multipart ({content_type or 'no content type'})")
    parts = decoder.MultipartDecoder(response.content, content_type).parts
    if len(parts) < 2:
        raise InferenceError("parse_response", f"Expected 2 parts, got {len(parts)}")
    try:
        returned_params = json.loads(parts[0].content.decode("utf-8").strip())
    except (UnicodeDecodeError, ValueError) as exc:
        raise InferenceError("parse_response", f"Part 0 is not JSON: {exc}") from exc
    disposition = parts[1].headers.get(b"Content-Disposition", b"").decode("utf-8")
    match = re.search(r'filename="([^"]+)"', disposition)
    return returned_params, parts[1].content, match.group(1) if match else "segmentation.nii.gz"


async def run_inference(model: str, image: str, params: dict[str, Any]) -> InferenceResult:
    """Start (or join) the inference job, wait for it and fetch its result.
    Cancelling the call cancels the job."""
    base_url = inference_api_url()
    timeout_s = job_timeout_s()
    job_id = inference_job_id(model, image, params)
    job_url = f"{base_url}/inference-jobs/{job_id}"
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        response = await shared_client().post(
            f"{base_url}/inference-jobs/",
            json={"job_id": job_id, "model": model, "image": image, "params": params,
                  "max_runtime_seconds": timeout_s},
            timeout=START_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise InferenceError("inference_request", f"MONAI Label could not be reached ({type(exc).__name__})") from exc
    if response.status_code >= 400:
        raise _http_failure("inference_request", response)
    try:
        job = await _await_job(job_url, timeout_s)
        result = await shared_client().get(f"{job_url}/result", timeout=RESULT_TIMEOUT)
    except asyncio.CancelledError:
        await _cancel_job(job_url)
        raise
    except httpx.HTTPError as exc:
        raise InferenceError("inference_request", f"MONAI Label could not be reached ({type(exc).__name__})") from exc
    if result.status_code >= 400:
        raise _http_failure("inference_result", result)
    returned_params, segmentation, filename = _parse_result(result)
    return InferenceResult(job, returned_params, segmentation, filename, loop.time() - started)


async def save_label(image: str, filename: str, label: bytes, params: dict[str, Any]) -> str:
    """Upload a label to the datastore (tag ``final``, which converts it to
    DICOM-SEG); returns the datastore's label id."""
    try:
        response = await shared_client().put(
            f"{inference_api_url()}/datastore/label",
            params={"image": image, "tag": "final"},
            files={
                "params": (None, json.dumps(params), "application/json"),
                "label": (filename, label, "application/octet-stream"),
            },
            timeout=UPLOAD_TIMEOUT,
        )
    except httpx.TimeoutException as exc:
        raise InferenceError("datastore_save", f"Upload timeout: {exc}") from exc
    except httpx.HTTPError as exc:
        raise InferenceError("datastore_save", f"MONAI Label could not be reached ({type(exc).__name__})") from exc
    if response.status_code != 200:
        raise _http_failure("datastore_save", response)
    return response.text.strip()


__all__ = [
    "InferenceError",
    "InferenceResult",
    "inference_job_id",
    "job_timeout_s",
    "poll_s",
    "run_inference",
    "save_label",
]
