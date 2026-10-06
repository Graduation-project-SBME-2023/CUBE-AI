"""Training-job receipts: committed before the job is submitted, so a
lost response never starts a second full training."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.training_jobs import TrainingJob
from application_api.db.models.workflow import Workflow
from application_api.services.publication import conflict

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _commit(db: AsyncSession, what: str) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise conflict("TRAINING_ACTIVE", f"{what} rejected: {exc.orig}") from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc


async def get_job(db: AsyncSession, job_id: str, *, lock: bool = False) -> TrainingJob:
    query = select(TrainingJob).where(TrainingJob.job_id == job_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    job = (await db.execute(query)).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail=f"Training job {job_id} not found")
    return job


async def list_jobs(db: AsyncSession, task_name: Optional[str] = None, limit: int = 100) -> list[TrainingJob]:
    query = select(TrainingJob).order_by(TrainingJob.created_at.desc()).limit(limit)
    if task_name:
        query = query.where(TrainingJob.task_name == task_name)
    return list((await db.execute(query)).scalars())


async def create_receipt(db: AsyncSession, task_name: str) -> TrainingJob:
    """Step 1: the receipt and the workflow freeze, in one commit. One
    active receipt per task (a partial unique index)."""
    workflow = (
        await db.execute(select(Workflow).where(Workflow.task_name == task_name).with_for_update())
    ).scalar_one_or_none()
    if workflow is None:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Task not found in workflow")
    active = (
        await db.execute(
            select(TrainingJob.job_id).where(TrainingJob.task_name == task_name, TrainingJob.ended_at.is_(None))
        )
    ).scalar()
    if active is not None:
        await db.rollback()
        raise conflict(
            "TRAINING_ACTIVE", f"Task {task_name!r} already has training job {active} in flight.", job_id=active
        )
    job = TrainingJob(job_id=uuid4().hex, task_name=task_name, created_at=_now())
    db.add(job)
    if workflow.frozen_at is None:
        # Freeze on admission, before the request leaves this process, so a
        # concurrent membership edit loses the race.
        workflow.frozen_at = func.now()
    await _commit(db, "Training receipt")
    return job


async def mark_submitted(db: AsyncSession, job_id: str) -> TrainingJob:
    """Step 3: MONAI acknowledged the job."""
    job = await get_job(db, job_id, lock=True)
    if job.submitted_at is None:
        job.submitted_at = _now()
    await _commit(db, "Training submission")
    return job


async def request_cancel(db: AsyncSession, job_id: str) -> TrainingJob:
    """The intent is durable, even while the submission is uncertain."""
    job = await get_job(db, job_id, lock=True)
    if job.ended_at is None and job.cancel_requested_at is None:
        job.cancel_requested_at = _now()
    await _commit(db, "Training cancel request")
    return job


async def record_outcome(
    db: AsyncSession, job_id: str, outcome: str, error: Optional[dict[str, Any]] = None
) -> TrainingJob:
    """A confirmed terminal report; recorded once."""
    job = await get_job(db, job_id, lock=True)
    if job.ended_at is None:
        job.outcome, job.ended_at, job.error = outcome, _now(), error
    await _commit(db, "Training outcome")
    return job
