"""Standalone nnUNet training through MONAI Label, with a durable receipt.

No transaction stays open across an HTTP call to MONAI:

1. commit the receipt and the workflow freeze;
2. POST the job (same id on every resubmission; MONAI dedupes by id);
3. commit `submitted_at`.

A lost response leaves the receipt uncertain (`submitted_at IS NULL`); the
reconciler (`reconcile`) then asks MONAI and resubmits the same id only on a 404.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.training_jobs import TrainingJob
from application_api.db.models.workflow import Workflow
from application_api.db.models.workflow_details import WorkflowDetail
from application_api.repositories import training_jobs as receipts

logger = logging.getLogger(__name__)

INFERENCE_API_URL = os.getenv("INFERENCE_API_URL", os.getenv("MONAI_URL", "http://monailabel:8000")).rstrip("/")
_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)

# MONAI's job statuses -> the receipt's confirmed outcome.
_TERMINAL = {"SUCCEEDED": "completed", "FAILED": "failed", "INTERRUPTED": "failed", "CANCELLED": "cancelled"}


def build_dataset_json(workflow: Workflow, rows) -> dict:
    """Regenerate nnUNet's dataset.json from `workflow` + frozen members."""
    definition = workflow.definition or {}
    channel_names = definition.get("channel_names") or definition.get("modality") or {}
    labels = definition.get("labels") or {}
    training_cases = sorted({row.case_index for row in rows if row.train})
    return {
        "name": workflow.display_title or workflow.task_name,
        "dataset_id": workflow.dataset_id,
        "description": workflow.description or "",
        "channel_names": channel_names,
        "labels": labels,
        "numTraining": len(training_cases),
        "file_ending": definition.get("file_ending", ".nii.gz"),
    }


def build_instance_mapping(rows) -> list[dict]:
    """Ordered (case, channel) -> series mapping, regenerated from the rows."""
    return [
        {
            "case_index": row.case_index,
            "channel_index": row.channel_index,
            "study_uid": row.study_uid,
            "volume_series_instance_uid": row.volume_series_instance_uid,
            "label_series_instance_uid": row.label_series_instance_uid,
            "subject_key": row.subject_key,
            "train": row.train,
        }
        for row in rows
    ]


async def build_payload(db: AsyncSession, task_name: str, job_id: str) -> dict[str, Any]:
    """The training request: ALL members of the task, in (case, channel) order.
    Never paginated: a capped, unsorted read trained on an arbitrary subset."""
    workflow = (await db.execute(select(Workflow).where(Workflow.task_name == task_name))).scalar_one_or_none()
    if workflow is None:
        raise HTTPException(status_code=404, detail="Task not found in workflow")
    rows = (
        await db.execute(
            select(
                WorkflowDetail.study_uid,
                WorkflowDetail.volume_series_instance_uid,
                WorkflowDetail.label_series_instance_uid,
                WorkflowDetail.modality,
                WorkflowDetail.train,
                WorkflowDetail.case_index,
                WorkflowDetail.channel_index,
                WorkflowDetail.subject_key,
            )
            .where(WorkflowDetail.task_name == task_name)
            .order_by(WorkflowDetail.case_index, WorkflowDetail.channel_index)
        )
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No records found for the given task")
    return {
        "job_id": job_id,
        "task_name": task_name,
        "dataset_id": workflow.dataset_id,
        "definition": workflow.definition or {},
        "records": [dict(row._mapping) for row in rows],
        "dataset_json": build_dataset_json(workflow, rows),
        "instance_mapping": build_instance_mapping(rows),
    }


async def _submit(db: AsyncSession, job: TrainingJob) -> Optional[str]:
    """POST the same id; returns MONAI's status, or None when the answer was lost."""
    payload = await build_payload(db, job.task_name, job.job_id)
    await db.commit()  # end the read transaction before the HTTP call
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(f"{INFERENCE_API_URL}/training-jobs/", json=payload)
    except httpx.HTTPError as exc:
        logger.warning("Training job %s: submission uncertain (%s)", job.job_id, exc)
        return None
    if response.status_code == 429:
        raise HTTPException(
            status_code=429,
            detail="Inference queue is full",
            headers={"Retry-After": response.headers.get("Retry-After", "10")},
        )
    if response.status_code >= 400:
        logger.warning("Training job %s: MONAI refused it (%s)", job.job_id, response.status_code)
        return None
    await receipts.mark_submitted(db, job.job_id)
    return response.json().get("status")


async def start_training(db: AsyncSession, task_name: str) -> tuple[TrainingJob, Optional[str]]:
    """Submit a training job. Returns the receipt and MONAI's status (None: submission uncertain,
    the reconciler finishes it)."""
    has_members = (
        await db.execute(select(WorkflowDetail.task_name).where(WorkflowDetail.task_name == task_name).limit(1))
    ).scalar()
    await db.rollback()
    if has_members is None:
        raise HTTPException(status_code=404, detail="No records found for the given task")
    job = await receipts.create_receipt(db, task_name)
    status = await _submit(db, job)
    return await receipts.get_job(db, job.job_id), status


async def _backend_status(job_id: str) -> tuple[Optional[int], dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{INFERENCE_API_URL}/training-jobs/{job_id}")
    except httpx.HTTPError:
        return None, {}
    body = response.json() if response.content and response.status_code < 500 else {}
    return response.status_code, body if isinstance(body, dict) else {}


async def reconcile(db: AsyncSession, job_id: str) -> tuple[TrainingJob, Optional[str]]:
    """Bring the receipt up to the backend's confirmed state: 404 on an
    unfinished receipt resubmits the same id; a terminal report is recorded."""
    job = await receipts.get_job(db, job_id)
    await db.commit()
    if job.ended_at is not None:
        return job, None
    code, body = await _backend_status(job_id)
    status = body.get("status")
    if code is None:
        return job, None  # unreachable: nothing is concluded
    if code == 404:
        if job.cancel_requested_at is not None:
            return await receipts.record_outcome(db, job_id, "cancelled"), None
        return await receipts.get_job(db, job_id), await _submit(db, job)
    if status in _TERMINAL:
        error = {"code": status.lower(), "detail": body.get("error")} if _TERMINAL[status] != "completed" else None
        return await receipts.record_outcome(db, job_id, _TERMINAL[status], error), status
    if job.submitted_at is None:
        job = await receipts.mark_submitted(db, job_id)
    return job, status


async def cancel(db: AsyncSession, job_id: str, purge: bool = False) -> tuple[TrainingJob, Optional[str]]:
    """Record the intent, ask MONAI to stop, and end the receipt only on a
    terminal report."""
    job = await receipts.request_cancel(db, job_id)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.delete(
                f"{INFERENCE_API_URL}/training-jobs/{job_id}", params={"purge": str(purge).lower()}
            )
        status = response.json().get("status") if response.content else None
    except httpx.HTTPError:
        return job, None
    if status in ("CANCELLED", "PURGED") or (status == "NOT_FOUND" and job.submitted_at is None):
        job = await receipts.record_outcome(db, job_id, "cancelled")
    elif status in _TERMINAL:
        job = await receipts.record_outcome(db, job_id, _TERMINAL[status])
    return job, status
