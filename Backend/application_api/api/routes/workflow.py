from fastapi import APIRouter, HTTPException, Depends, Query, Path, status
from pydantic import BaseModel
import logging
from typing import Optional, List

from sqlalchemy import delete as sa_delete, func, select, text, tuple_, update as sa_update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.db.models.patient import Patient
from application_api.db.models.study import Study
from application_api.db.models.workflow import Workflow
from application_api.db.models.workflow_details import WorkflowDetail
from application_api.services import training


# The nnUNet dataset id must be allocated under a lock plus the UNIQUE
# constraint on workflow.dataset_id — never from the max local directory,
# which cannot see a concurrent allocation or a task whose directory has not
# been created yet. One fixed advisory-lock key serialises allocation.
_DATASET_ID_LOCK_KEY = 0x0D57A1D0


async def _allocate_dataset_id(db: AsyncSession) -> int:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _DATASET_ID_LOCK_KEY}
    )
    current_max = (
        await db.execute(select(func.max(Workflow.dataset_id)))
    ).scalar_one_or_none()
    return int(current_max or 0) + 1


async def _require_unfrozen(db: AsyncSession, task_name: str) -> Workflow:
    """Membership and definition are immutable once training is admitted."""
    workflow = (
        await db.execute(select(Workflow).where(Workflow.task_name == task_name))
    ).scalar_one_or_none()
    if workflow is None:
        raise HTTPException(
            status_code=400,
            detail=f"Task name '{task_name}' does not exist as a workflow.",
        )
    if workflow.frozen_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Workflow '{task_name}' was frozen at {workflow.frozen_at.isoformat()} "
                "because a training job was admitted against it. Its membership and "
                "definition cannot change; create a new task instead."
            ),
        )
    return workflow


logger = logging.getLogger(__name__)

# Define router for workflow operations
router = APIRouter(
    prefix="/workflow",
    tags=["Workflow"],
    responses={404: {"description": "Not found"}},
)



# Define the Pydantic model for validation
class WorkflowRequest(BaseModel):
    study_uid: str
    task_name: str
    series_instance_uid: str
    instance_uid: Optional[str] = None
    train: Optional[bool] = None


# New Pydantic model for updating series_instance_uid and instance_uid
class WorkflowUpdateUIDRequest(BaseModel):
    study_uid: str
    task_name: str
    series_instance_uid: Optional[str] = None
    instance_uid: Optional[str] = None
    modality: Optional[str] = None


class WorkflowDeleteRequest(BaseModel):
    study_uid: str
    task_name: str


# New Pydantic model for deleting multiple records
class WorkflowDeleteMultipleRequest(BaseModel):
    records: List[WorkflowDeleteRequest]


class ListWorkflowRequest(BaseModel):
    task_name: str



@router.post("/{task_name}/add", summary="Add a new workflow record for a specific task")
async def add_workflow(
    data: WorkflowRequest,
    task_name: str = Path(..., description="Task name"),
    db: AsyncSession = Depends(get_db),
):
    if data.task_name != task_name:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Path task_name '{task_name}' does not match body task_name "
                f"'{data.task_name}'."
            ),
        )

    await _require_unfrozen(db, task_name)

    # Case_index is the deterministic training order. A new member takes
    # the next index for its task; channels of one case share the index and
    # differ by channel_index (this endpoint adds single-channel members).
    next_case_index = int(
        (
            await db.execute(
                select(func.coalesce(func.max(WorkflowDetail.case_index), 0)).where(
                    WorkflowDetail.task_name == task_name
                )
            )
        ).scalar_one()
    ) + 1

    # subject_key is the patient-level split group, so two series from one
    # patient can never land on both sides of a split.
    subject_key = (
        await db.execute(
            select(Patient.orthanc_patient_id)
            .select_from(Study)
            .join(Patient, Patient.patient_id == Study.patient_id)
            .where(Study.study_uid == data.study_uid)
        )
    ).scalar_one_or_none()

    detail = WorkflowDetail(
        task_name=task_name,
        study_uid=data.study_uid,
        volume_series_instance_uid=data.series_instance_uid,
        label_series_instance_uid=data.instance_uid,
        annotated=bool(data.instance_uid),
        train=True,
        case_index=next_case_index,
        channel_index=0,
        subject_key=subject_key,
    )
    db.add(detail)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Study already exists for the given task: {e}",
        )
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Error adding workflow record: %s", e)
        raise HTTPException(status_code=500, detail="Failed to add workflow record")
    return {"message": "Workflow record added successfully"}


@router.post("/update-uids", summary="Update data of a workflow record")
async def update_workflow_uids(
    data: WorkflowUpdateUIDRequest, db: AsyncSession = Depends(get_db)
):
    update_values = {}
    if data.series_instance_uid is not None:
        update_values["volume_series_instance_uid"] = data.series_instance_uid
    if data.instance_uid is not None:
        update_values["label_series_instance_uid"] = data.instance_uid
    if data.modality is not None:
        update_values["modality"] = data.modality

    if not update_values:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Membership is frozen once a training job is admitted.
    await _require_unfrozen(db, data.task_name)

    update_values["annotated"] = True

    try:
        result = await db.execute(
            sa_update(WorkflowDetail)
            .where(
                WorkflowDetail.study_uid == data.study_uid,
                WorkflowDetail.task_name == data.task_name,
            )
            .values(**update_values)
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Workflow record not found")
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Error updating workflow record: %s", e)
        raise HTTPException(status_code=500, detail="Failed to update workflow record")
    return {"message": "Workflow record updated successfully"}


@router.post(
    "/delete-multiple",
    summary="Delete multiple workflow records by study_uid and task_name",
)
async def delete_multiple_workflows(
    data: WorkflowDeleteMultipleRequest, db: AsyncSession = Depends(get_db)
):
    pairs = [(record.study_uid, record.task_name) for record in data.records]
    if not pairs:
        return {"message": "No matching workflow records found", "deleted": 0}

    for task_name in {record.task_name for record in data.records}:
        await _require_unfrozen(db, task_name)

    try:
        result = await db.execute(
            sa_delete(WorkflowDetail).where(
                tuple_(WorkflowDetail.study_uid, WorkflowDetail.task_name).in_(pairs)
            )
        )
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Error deleting workflow records: %s", e)
        raise HTTPException(status_code=500, detail="Failed to delete workflow records")

    deleted_count = result.rowcount or 0
    if deleted_count == 0:
        return {"message": "No matching workflow records found", "deleted": 0}
    return {"message": f"{deleted_count} workflow records deleted successfully", "deleted": deleted_count}


@router.post("/{task_name}/filter-uid", summary="Filter by task_name and retrieve only study_uid")
async def filter_study_uid_by_task_name(
    task_name: str = Path(..., description="Workflow Name to filter by."),
    skip: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await db.execute(
            select(WorkflowDetail.study_uid)
            .where(WorkflowDetail.task_name == task_name)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()
    except SQLAlchemyError as e:
        logger.error("Filter error: %s", e)
        raise HTTPException(status_code=500, detail="Filter operation failed")


@router.post(
    "/{task_name}/filter-annotated", summary="Filter by task_name and retrieve annotation status"
)
async def filter_series_id_by_task_name(
    task_name: str = Path(..., description="task name to filter by"),
    skip: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await db.execute(
            select(WorkflowDetail.study_uid, WorkflowDetail.annotated)
            .where(WorkflowDetail.task_name == task_name)
            .offset(skip)
            .limit(limit)
        )
        return {study_uid: annotated for study_uid, annotated in result.all()}
    except SQLAlchemyError as e:
        logger.error("Filter error: %s", e)
        raise HTTPException(status_code=500, detail="Filter operation failed")


@router.post(
    "/{task_name}/filter",
    summary="Filter by task_name and retrieve study_uid, volume_series_instance_uid, label_series_instance_uid, and modality",
)
async def filter_workflow_data(
    task_name: str = Path(..., description="Task name to filter by"),
    skip: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await db.execute(
            select(
                WorkflowDetail.study_uid,
                WorkflowDetail.volume_series_instance_uid,
                WorkflowDetail.label_series_instance_uid,
                WorkflowDetail.modality,
            )
            .where(WorkflowDetail.task_name == task_name)
            .offset(skip)
            .limit(limit)
        )
        rows = result.all()
        return {
            "study_uid": [row.study_uid for row in rows],
            "series_instance_uid": [row.volume_series_instance_uid for row in rows],
            "instance_uid": [row.label_series_instance_uid for row in rows],
            "modality": [row.modality for row in rows],
        }
    except SQLAlchemyError as e:
        logger.error("Filter error: %s", e)
        raise HTTPException(status_code=500, detail="Filter operation failed")


@router.post("/add", summary="Add a new task to workflow")
async def add_task_to_workflow(data: ListWorkflowRequest, db: AsyncSession = Depends(get_db)):
    dataset_id = await _allocate_dataset_id(db)
    db.add(Workflow(task_name=data.task_name, dataset_id=dataset_id))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Task '{data.task_name}' already exists")
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Error adding task to workflow: %s", e)
        raise HTTPException(status_code=500, detail="Failed to add task to workflow")
    return {"message": "Task added to workflow successfully"}


@router.get("/", summary="Fetch all records from workflow")
async def get_all_workflow(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await db.execute(
            select(
                Workflow.task_name,
                Workflow.dataset_id,
                Workflow.display_title,
                Workflow.status,
                Workflow.frozen_at,
            )
            .order_by(Workflow.task_name)
            .offset(skip)
            .limit(limit)
        )
        return [
            {
                "task_name": row.task_name,
                "dataset_id": row.dataset_id,
                "display_title": row.display_title,
                "status": row.status,
                "frozen_at": row.frozen_at,
            }
            for row in result.all()
        ]
    except SQLAlchemyError as e:
        logger.error("Error fetching workflow records: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve workflow records")


@router.delete("/delete", summary="Delete a workflow")
async def delete_workflow(data: ListWorkflowRequest, db: AsyncSession = Depends(get_db)):
    await _require_unfrozen(db, data.task_name)
    try:
        result = await db.execute(sa_delete(Workflow).where(Workflow.task_name == data.task_name))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Task not found in workflow")
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Error deleting task from workflow: %s", e)
        raise HTTPException(status_code=500, detail="Failed to delete task from workflow")
    return {"message": "Task deleted successfully"}


@router.post("/{task_name}/update-train", summary="Update train flag for a workflow record")
async def update_train_flag(
    task_name: str = Path(..., description="Task name to filter records"),
    study_uid: str = Query(..., description="Study Instance UID to identify record"),
    train: bool = Query(..., description="New train flag value"),
    db: AsyncSession = Depends(get_db),
):
    # Membership — which includes whether a case trains — is frozen once
    # a training job is admitted.
    await _require_unfrozen(db, task_name)
    try:
        result = await db.execute(
            sa_update(WorkflowDetail)
            .where(
                WorkflowDetail.task_name == task_name,
                WorkflowDetail.study_uid == study_uid,
            )
            .values(train=train)
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Workflow record not found")
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("Error updating train flag: %s", e)
        raise HTTPException(status_code=500, detail="Failed to update train flag")
    return {"message": "Train flag updated successfully"}


@router.get("/{task_name}/get-train", summary="Fetch train flag for a workflow record")
async def get_train_flag(
    task_name: str = Path(..., description="Task name to filter records"),
    study_uid: str = Query(..., description="Case ID to identify record"),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = (
            await db.execute(
                select(WorkflowDetail.train).where(
                    WorkflowDetail.task_name == task_name,
                    WorkflowDetail.study_uid == study_uid,
                )
            )
        ).scalar_one_or_none()
    except SQLAlchemyError as e:
        logger.error("Error fetching train flag: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch train flag")
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow record not found")
    return {"train": result}


@router.post(
    "/{task_name}/train",
    summary="Start nnUNet training for a specific workflow",
    status_code=status.HTTP_202_ACCEPTED,
)
async def initialize_training(
    task_name: str = Path(..., description="Task name to start training for"),
    db: AsyncSession = Depends(get_db),
):
    """Start nnUNet training over the complete, frozen, deterministic member set,
    with a receipt committed before the submission (`services/training.py`)."""
    job, backend_status = await training.start_training(db, task_name)
    return {
        "job_id": job.job_id,
        "task_name": task_name,
        "status": backend_status or "SUBMISSION_UNCERTAIN",
    }


@router.get("/training-jobs/{job_id}", summary="Get training job status")
async def get_training_job(job_id: str, db: AsyncSession = Depends(get_db)):
    """Reconciles the receipt with MONAI on every read."""
    job, backend_status = await training.reconcile(db, job_id)
    return {"job_id": job.job_id, "task_name": job.task_name, "status": backend_status,
            "outcome": job.outcome, "submitted_at": job.submitted_at, "ended_at": job.ended_at}


@router.delete("/training-jobs/{job_id}", summary="Cancel or purge a training job")
async def cancel_training_job(job_id: str, purge: bool = Query(False), db: AsyncSession = Depends(get_db)):
    job, backend_status = await training.cancel(db, job_id, purge)
    return {"job_id": job.job_id, "status": backend_status, "outcome": job.outcome}
