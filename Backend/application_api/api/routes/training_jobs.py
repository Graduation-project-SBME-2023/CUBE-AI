from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.repositories import training_jobs as receipts
from application_api.schemas.training_jobs import TrainingJobCreate, TrainingJobOut
from application_api.services import training

router = APIRouter(prefix="/training-jobs", tags=["Training jobs"])


def _out(job, backend_status: Optional[str]) -> TrainingJobOut:
    out = TrainingJobOut.model_validate(job)
    out.backend_status = backend_status
    return out


@router.post("/", response_model=TrainingJobOut, status_code=status.HTTP_202_ACCEPTED)
async def start_training(data: TrainingJobCreate, db: AsyncSession = Depends(get_db)):
    """Receipt + freeze, then the submission, then `submitted_at`. A lost
    response leaves the receipt uncertain; GET reconciles it."""
    return _out(*await training.start_training(db, data.task_name))


@router.get("/", response_model=list[TrainingJobOut])
async def list_training_jobs(
    task_name: Optional[str] = None, limit: int = Query(100, ge=1, le=500), db: AsyncSession = Depends(get_db)
):
    return await receipts.list_jobs(db, task_name, limit)


@router.get("/{job_id}", response_model=TrainingJobOut)
async def read_training_job(job_id: str, db: AsyncSession = Depends(get_db)):
    """The receipt, reconciled with MONAI (404 there -> the same id is resubmitted)."""
    return _out(*await training.reconcile(db, job_id))


@router.delete("/{job_id}", response_model=TrainingJobOut)
async def cancel_training_job(job_id: str, purge: bool = False, db: AsyncSession = Depends(get_db)):
    """Record the cancel intent, ask MONAI to stop; ended only on a terminal report."""
    return _out(*await training.cancel(db, job_id, purge))
