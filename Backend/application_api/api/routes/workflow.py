from fastapi import APIRouter, HTTPException, Depends, Query, Path, status
from pydantic import BaseModel
import httpx
import logging
from typing import Optional, List
import os
from uuid import uuid4

from sqlalchemy import delete as sa_delete, func, select, tuple_, update as sa_update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.db.models.workflow import Workflow
from application_api.db.models.workflow_details import WorkflowDetail


logger = logging.getLogger(__name__)

# Define router for workflow operations
router = APIRouter(
    prefix="/workflow",
    tags=["Workflow"],
    responses={404: {"description": "Not found"}},
)

INFERENCE_API_URL = os.getenv(
    "INFERENCE_API_URL",
    os.getenv("MONAI_URL", "http://monailabel:8000"),
).rstrip("/")


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

    task_exists = (
        await db.execute(select(Workflow.task_name).where(Workflow.task_name == task_name))
    ).scalar_one_or_none()
    if not task_exists:
        raise HTTPException(
            status_code=400,
            detail=f"Task name '{task_name}' does not exist as a workflow.",
        )

    detail = WorkflowDetail(
        task_name=task_name,
        study_uid=data.study_uid,
        volume_series_instance_uid=data.series_instance_uid,
        label_series_instance_uid=data.instance_uid,
        annotated=bool(data.instance_uid),
        train=True,
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
    db.add(Workflow(task_name=data.task_name))
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
            select(Workflow.task_name).order_by(Workflow.task_name).offset(skip).limit(limit)
        )
        return [{"task_name": task_name} for task_name in result.scalars().all()]
    except SQLAlchemyError as e:
        logger.error("Error fetching workflow records: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve workflow records")


@router.delete("/delete", summary="Delete a workflow")
async def delete_workflow(data: ListWorkflowRequest, db: AsyncSession = Depends(get_db)):
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
                WorkflowDetail.train,
            )
            .where(WorkflowDetail.task_name == task_name)
            .offset(skip)
            .limit(limit)
        )
        rows = result.all()
        if not rows:
            raise HTTPException(status_code=404, detail="No records found for the given task")

        job_id = uuid4().hex
        payload = {
            "job_id": job_id,
            "task_name": task_name,
            "records": [dict(row._mapping) for row in rows],
        }
        timeout = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{INFERENCE_API_URL}/training-jobs/", json=payload)
        if response.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail="Inference queue is full",
                headers={"Retry-After": response.headers.get("Retry-After", "10")},
            )
        response.raise_for_status()
        job = response.json()
        return {"job_id": job_id, "task_name": task_name, "status": job["status"]}
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        logger.error("Inference service unavailable while starting training: %s", exc)
        raise HTTPException(status_code=503, detail="Inference service is unavailable") from exc
    except SQLAlchemyError as exc:
        logger.exception("Database error starting nnUNet training for %s", task_name)
        raise HTTPException(status_code=500, detail="Failed to initialize nnUNet training") from exc


@router.get("/training-jobs/{job_id}", summary="Get training job status")
async def get_training_job(job_id: str):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{INFERENCE_API_URL}/training-jobs/{job_id}")
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Training job not found")
        response.raise_for_status()
        return response.json()
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Inference service is unavailable") from exc


@router.delete("/training-jobs/{job_id}", summary="Cancel or purge a training job")
async def cancel_training_job(job_id: str, purge: bool = Query(False)):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.delete(
                f"{INFERENCE_API_URL}/training-jobs/{job_id}",
                params={"purge": str(purge).lower()},
            )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Inference service is unavailable") from exc
