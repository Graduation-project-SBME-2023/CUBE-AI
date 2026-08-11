import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from services.job_capacity import inference_capacity
from storage_paths import JOBS_DIR, TEMP_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inference-jobs", tags=["Inference Jobs"])

_INSTANCE_ID = str(uuid4())
_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_JOB_ROOT = JOBS_DIR
_JOB_TEMP_ROOT = TEMP_DIR / "radiology" / "inference_jobs"
_SELF_URL = os.environ.get("MONAI_SELF_URL", "http://127.0.0.1:8000").rstrip("/")


def _default_runtime() -> float:
    try:
        value = float(os.environ.get("SEGMENTATION_JOB_TIMEOUT_SECONDS", "1800"))
    except (TypeError, ValueError):
        return 1800.0
    return value if 0 < value <= 7200 else 1800.0


_DEFAULT_RUNTIME = _default_runtime()
_tasks: dict[str, asyncio.Task] = {}
_start_lock = asyncio.Lock()


def interrupt_stale_jobs() -> None:
    _JOB_ROOT.mkdir(parents=True, exist_ok=True)
    for metadata_path in _JOB_ROOT.glob("*.json"):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            if payload.get("status") not in {"QUEUED", "RUNNING", "CANCELLING"}:
                continue
            payload.update(
                {
                    "status": "INTERRUPTED",
                    "error": "Inference service restarted before the job completed",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            logger.warning("Unable to recover inference job metadata %s", metadata_path)


interrupt_stale_jobs()


class InferenceJobRequest(BaseModel):
    job_id: str
    model: str
    image: str
    params: dict = Field(default_factory=dict)
    max_runtime_seconds: float = Field(default=_DEFAULT_RUNTIME, gt=0, le=7200)


def _validate_job_id(job_id: str) -> str:
    if not _JOB_ID_PATTERN.fullmatch(job_id):
        raise HTTPException(status_code=400, detail="Invalid inference job ID")
    return job_id


def _paths(job_id: str) -> tuple[Path, Path, Path]:
    _validate_job_id(job_id)
    _JOB_ROOT.mkdir(parents=True, exist_ok=True)
    _JOB_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    return (
        _JOB_ROOT / f"{job_id}.json",
        _JOB_ROOT / f"{job_id}.result",
        _JOB_TEMP_ROOT / f"{job_id}.result.tmp",
    )


def _read_job(job_id: str) -> dict | None:
    metadata_path, _, _ = _paths(job_id)
    if not metadata_path.exists():
        return None
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Corrupt inference job: {exc}")


def _write_job(job_id: str, payload: dict) -> dict:
    metadata_path, _, _ = _paths(job_id)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    temporary_path = _JOB_TEMP_ROOT / f"{job_id}.json.tmp"
    temporary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary_path.replace(metadata_path)
    return payload


def _input_hash(request: InferenceJobRequest) -> str:
    canonical = json.dumps(
        {
            "model": request.model,
            "image": request.image,
            "params": request.params,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _stream_inference(request: InferenceJobRequest, result_tmp: Path) -> tuple[int, str]:
    timeout = httpx.Timeout(
        connect=15.0,
        read=request.max_runtime_seconds,
        write=60.0,
        pool=15.0,
    )
    files = {
        "params": (
            None,
            json.dumps(request.params),
            "application/json",
        )
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{_SELF_URL}/infer/{request.model}",
            params={"image": request.image, "output": "all"},
            files=files,
        ) as response:
            content_type = response.headers.get("content-type", "application/octet-stream")
            with result_tmp.open("wb") as output:
                async for chunk in response.aiter_bytes():
                    output.write(chunk)
            return response.status_code, content_type


async def _execute_job(request: InferenceJobRequest) -> None:
    metadata_path, result_path, result_tmp = _paths(request.job_id)
    del metadata_path
    job = _read_job(request.job_id) or {}
    try:
        async with inference_capacity.execution():
            job.update(
                {
                    "status": "RUNNING",
                    "owner_instance_id": _INSTANCE_ID,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "attempt": int(job.get("attempt") or 0) + 1,
                    "error": None,
                }
            )
            _write_job(request.job_id, job)
            status_code, content_type = await asyncio.wait_for(
                _stream_inference(request, result_tmp),
                timeout=request.max_runtime_seconds,
            )
            if status_code != 200:
                error_preview = result_tmp.read_bytes()[:500].decode("utf-8", errors="replace")
                job.update(
                    {
                        "status": "FAILED",
                        "status_code": status_code,
                        "error": f"MONAI inference returned HTTP {status_code}: {error_preview}",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                result_tmp.unlink(missing_ok=True)
            else:
                result_tmp.replace(result_path)
                job.update(
                    {
                        "status": "SUCCEEDED",
                        "status_code": status_code,
                        "content_type": content_type,
                        "result_size": result_path.stat().st_size,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
    except asyncio.TimeoutError:
        result_tmp.unlink(missing_ok=True)
        job.update(
            {
                "status": "TIMED_OUT",
                "error": f"Inference exceeded {request.max_runtime_seconds:.0f}s",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except asyncio.CancelledError:
        result_tmp.unlink(missing_ok=True)
        job.update(
            {
                "status": "CANCELLED",
                "error": "Inference job was cancelled",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        raise
    except Exception as exc:
        result_tmp.unlink(missing_ok=True)
        logger.exception("Inference job %s failed", request.job_id)
        job.update(
            {
                "status": "FAILED",
                "error": f"{type(exc).__name__}: {exc}",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    finally:
        _write_job(request.job_id, job)
        _tasks.pop(request.job_id, None)


@router.post("/", status_code=status.HTTP_202_ACCEPTED)
async def start_inference_job(request: InferenceJobRequest) -> dict:
    _validate_job_id(request.job_id)
    request_hash = _input_hash(request)

    async with _start_lock:
        existing = _read_job(request.job_id)
        if existing and existing.get("input_hash") != request_hash:
            raise HTTPException(
                status_code=409,
                detail="Inference job ID is already bound to different inputs",
            )
        if existing and existing.get("status") == "SUCCEEDED":
            return existing

        task = _tasks.get(request.job_id)
        if task and not task.done():
            return existing or {"job_id": request.job_id, "status": "RUNNING"}

        if not await inference_capacity.reserve():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Inference queue is full",
                headers={"Retry-After": "10"},
            )

        now = datetime.now(timezone.utc).isoformat()
        job = existing or {
            "job_id": request.job_id,
            "created_at": now,
            "attempt": 0,
        }
        job.update(
            {
                "status": "QUEUED",
                "input_hash": request_hash,
                "model": request.model,
                "image": request.image,
                "max_runtime_seconds": request.max_runtime_seconds,
                "owner_instance_id": _INSTANCE_ID,
            }
        )
        _write_job(request.job_id, job)
        _tasks[request.job_id] = asyncio.create_task(
            _execute_job(request),
            name=f"inference-job:{request.job_id}",
        )
        # Let the task enter the capacity context before cancellation is possible.
        await asyncio.sleep(0)
        return job


@router.get("/{job_id}")
async def get_inference_job(job_id: str) -> dict:
    job = _read_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Inference job not found")
    return job


@router.get("/{job_id}/result")
async def get_inference_result(job_id: str):
    job = _read_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Inference job not found")
    if job.get("status") != "SUCCEEDED":
        raise HTTPException(status_code=409, detail=f"Job is {job.get('status')}")
    _, result_path, _ = _paths(job_id)
    if not result_path.is_file():
        raise HTTPException(status_code=500, detail="Inference result is missing")
    return FileResponse(
        result_path,
        media_type=job.get("content_type") or "application/octet-stream",
    )


@router.delete("/{job_id}")
async def cancel_or_purge_inference_job(
    job_id: str,
    purge: bool = Query(False),
) -> dict:
    job = _read_job(job_id)
    if not job:
        return {"job_id": job_id, "status": "NOT_FOUND"}

    task = _tasks.get(job_id)
    if task and not task.done():
        task.cancel()
        return {"job_id": job_id, "status": "CANCELLING"}

    if purge:
        metadata_path, result_path, result_tmp = _paths(job_id)
        metadata_path.unlink(missing_ok=True)
        result_path.unlink(missing_ok=True)
        result_tmp.unlink(missing_ok=True)
        return {"job_id": job_id, "status": "PURGED"}

    job["status"] = "CANCELLED"
    job["error"] = "Inference job was cancelled"
    _write_job(job_id, job)
    return job
