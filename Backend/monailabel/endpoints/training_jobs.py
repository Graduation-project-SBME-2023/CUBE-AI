import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from endpoints.my_endpoints_datastore import (
    create_nnunet_dataset,
    download_images,
    download_labels,
    prepare_instance_mapping,
)
from services.job_capacity import inference_capacity
from storage_paths import JOBS_DIR, TEMP_DIR


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/training-jobs", tags=["Training Jobs"])

_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_JOB_ROOT = JOBS_DIR / "training"
_TEMP_ROOT = TEMP_DIR / "radiology" / "training_jobs"
_tasks: dict[str, asyncio.Task] = {}
_start_lock = asyncio.Lock()


class TrainingRecord(BaseModel):
    study_uid: str
    volume_series_instance_uid: str
    label_series_instance_uid: str | None = None
    modality: str | None = None
    train: bool = True


class TrainingJobRequest(BaseModel):
    job_id: str
    task_name: str = Field(min_length=1, max_length=128)
    records: list[TrainingRecord] = Field(min_length=1)


def _validate_job_id(job_id: str) -> str:
    if not _JOB_ID_PATTERN.fullmatch(job_id):
        raise HTTPException(status_code=400, detail="Invalid training job ID")
    return job_id


def _metadata_path(job_id: str) -> Path:
    _validate_job_id(job_id)
    _JOB_ROOT.mkdir(parents=True, exist_ok=True)
    _TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    return _JOB_ROOT / f"{job_id}.json"


def _read_job(job_id: str) -> dict | None:
    path = _metadata_path(job_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Corrupt training job: {exc}") from exc


def _write_job(job_id: str, payload: dict) -> dict:
    path = _metadata_path(job_id)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    temporary = _TEMP_ROOT / f"{job_id}.json.tmp"
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)
    return payload


def _request_hash(request: TrainingJobRequest) -> str:
    canonical = request.model_dump_json(exclude={"job_id"})
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _prepare_training(request: TrainingJobRequest) -> dict:
    records = [record.model_dump() for record in request.records]
    download_images([record["volume_series_instance_uid"] for record in records])
    label_uids = [record["label_series_instance_uid"] for record in records if record["label_series_instance_uid"]]
    if label_uids:
        download_labels(label_uids)
    prepare_instance_mapping(request.task_name, records)
    task_dir, dataset_json_path = create_nnunet_dataset(request.task_name)
    return {
        "task_dir": str(task_dir),
        "dataset_json_path": str(dataset_json_path),
        "records": len(records),
    }


async def _execute(request: TrainingJobRequest) -> None:
    job = _read_job(request.job_id) or {}
    try:
        async with inference_capacity.execution():
            latest = _read_job(request.job_id) or job
            if latest.get("status") == "CANCELLING":
                job.update(
                    {
                        "status": "CANCELLED",
                        "error": "Training job was cancelled before execution",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                return
            job.update(
                {
                    "status": "RUNNING",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "attempt": int(job.get("attempt") or 0) + 1,
                    "error": None,
                }
            )
            _write_job(request.job_id, job)
            result = await asyncio.to_thread(_prepare_training, request)
            latest = _read_job(request.job_id) or job
            if latest.get("status") == "CANCELLING":
                job.update(
                    {
                        "status": "CANCELLED",
                        "error": "Training cancellation completed after the active preparation step",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            else:
                job.update(
                    {
                        "status": "SUCCEEDED",
                        "result": result,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
    except asyncio.CancelledError:
        job.update(
            {
                "status": "CANCELLED",
                "error": "Training job was cancelled",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        raise
    except Exception as exc:
        logger.exception("Training job %s failed", request.job_id)
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


def interrupt_stale_jobs() -> None:
    _JOB_ROOT.mkdir(parents=True, exist_ok=True)
    for path in _JOB_ROOT.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("status") not in {"QUEUED", "RUNNING", "CANCELLING"}:
                continue
            payload.update(
                {
                    "status": "INTERRUPTED",
                    "error": "Inference service restarted before the training job completed",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            logger.warning("Unable to recover training job metadata %s", path)


interrupt_stale_jobs()


@router.post("/", status_code=status.HTTP_202_ACCEPTED)
async def start_training_job(request: TrainingJobRequest) -> dict:
    request_hash = _request_hash(request)
    async with _start_lock:
        existing = _read_job(request.job_id)
        if existing and existing.get("input_hash") != request_hash:
            raise HTTPException(status_code=409, detail="Training job ID is bound to different inputs")
        if existing and existing.get("status") == "SUCCEEDED":
            return existing
        task = _tasks.get(request.job_id)
        if task and not task.done():
            return existing or {"job_id": request.job_id, "status": "RUNNING"}
        if not await inference_capacity.reserve():
            raise HTTPException(
                status_code=429,
                detail="Inference queue is full",
                headers={"Retry-After": "10"},
            )

        job = existing or {
            "job_id": request.job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "attempt": 0,
        }
        job.update(
            {
                "status": "QUEUED",
                "input_hash": request_hash,
                "task_name": request.task_name,
                "record_count": len(request.records),
            }
        )
        _write_job(request.job_id, job)
        _tasks[request.job_id] = asyncio.create_task(
            _execute(request),
            name=f"training-job:{request.job_id}",
        )
        await asyncio.sleep(0)
        return job


@router.get("/{job_id}")
async def get_training_job(job_id: str) -> dict:
    job = _read_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Training job not found")
    return job


@router.delete("/{job_id}")
async def cancel_or_purge_training_job(job_id: str, purge: bool = Query(False)) -> dict:
    job = _read_job(job_id)
    if not job:
        return {"job_id": job_id, "status": "NOT_FOUND"}
    task = _tasks.get(job_id)
    if task and not task.done():
        job.update({"status": "CANCELLING", "error": "Cancellation requested"})
        return _write_job(job_id, job)
    if purge:
        _metadata_path(job_id).unlink(missing_ok=True)
        return {"job_id": job_id, "status": "PURGED"}
    job.update({"status": "CANCELLED", "error": "Training job was cancelled"})
    return _write_job(job_id, job)
