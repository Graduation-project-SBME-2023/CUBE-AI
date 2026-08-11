from fastapi import APIRouter, HTTPException, Depends, Query, Path, status
from pydantic import BaseModel
import asyncpg
import httpx
import logging
import time
from typing import Optional, List
import os
from uuid import uuid4

from core.database import engine
from core.metrics import observe_pool_acquire, observe_sql_query


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
    train: Optional[bool]


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


class TaskNameFilterRequest(BaseModel):
    task_name: str


class ListWorkflowRequest(BaseModel):
    task_name: str


class AnnotationFilterRequest(BaseModel):
    task_name: str


class InstrumentedWorkflowConnection:
    """Expose the legacy asyncpg API while using the Application API pool."""

    def __init__(self, connection):
        self._connection = connection

    async def _measure(self, operation, *args):
        started = time.perf_counter()
        try:
            return await operation(*args)
        finally:
            observe_sql_query(time.perf_counter() - started)

    async def execute(self, *args):
        return await self._measure(self._connection.execute, *args)

    async def fetch(self, *args):
        return await self._measure(self._connection.fetch, *args)

    async def fetchval(self, *args):
        return await self._measure(self._connection.fetchval, *args)

    def transaction(self):
        return self._connection.transaction()


# Reuse the configured SQLAlchemy/asyncpg pool instead of opening a connection per request.
async def get_db_connection():
    started = time.perf_counter()
    async with engine.connect() as pooled_connection:
        observe_pool_acquire(time.perf_counter() - started)
        raw_connection = await pooled_connection.get_raw_connection()
        yield InstrumentedWorkflowConnection(raw_connection.driver_connection)


@router.post("/{task_name}/add", summary="Add a new workflow record for a specific task")
async def add_workflow(data: WorkflowRequest = None, task_name: str = Path(..., description="Task name"), conn=Depends(get_db_connection)):
    async with conn.transaction():
        # Step 1: Ensure the task_name exists in workflow
        task_name_exists = await conn.fetchval(
            """
            SELECT EXISTS(SELECT 1 FROM workflow WHERE task_name = $1)
        """,
            task_name,
        )

        if not task_name_exists:
            raise HTTPException(
                status_code=400,
                detail=f"Task name '{data.task_name}' does not exist as a workflow.",
            )

        # Step 2: Insert into workflow
        query = """
        INSERT INTO workflow_details (study_uid, task_name, volume_series_instance_uid, label_series_instance_uid, annotated, train)
        VALUES ($1, $2, $3, $4, $5, $6)
        """
        try:
            await conn.execute(
                query,
                data.study_uid,
                data.task_name,
                data.series_instance_uid,
                data.instance_uid if data.instance_uid else None,
                True if data.instance_uid else False,
                True,
            )
            return {"message": "Workflow record added successfully"}
        except asyncpg.exceptions.UniqueViolationError as e:
            raise HTTPException(
                status_code=409,  # Conflict
                detail=f"Study already exists for the given task: {e}",
            )
        except Exception as e:
            logger.error(f"Error adding workflow record: {e}")
            raise HTTPException(status_code=500, detail="Failed to add workflow record")


@router.post("/update-uids", summary="Update data of a workflow record")
async def update_workflow_uids(
    data: WorkflowUpdateUIDRequest, conn=Depends(get_db_connection)
):
    # Build the update query dynamically based on provided fields
    update_fields = []
    params = []
    if data.series_instance_uid is not None:
        params.append(data.series_instance_uid)
        update_fields.append(f"volume_series_instance_uid = ${len(params)}")
    if data.instance_uid is not None:
        params.append(data.instance_uid)
        update_fields.append(f"label_series_instance_uid = ${len(params)}")
    if data.modality is not None:
        params.append(data.modality)
        update_fields.append(f"modality = ${len(params)}")

    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    update_fields.append("annotated = TRUE")

    # Add study_uid to params for the WHERE clause
    params.append(data.study_uid)
    study_uid_idx = len(params)
    params.append(data.task_name)
    task_name_idx = len(params)

    query = f"""
    UPDATE workflow_details
    SET {', '.join(update_fields)}
    WHERE study_uid = ${study_uid_idx} AND task_name = ${task_name_idx}
    """
    try:
        result = await conn.execute(query, *params)
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Workflow record not found")
        return {"message": "Workflow record updated successfully"}
    except Exception as e:
        logger.error(f"Error updating workflow record: {e}")
        raise HTTPException(status_code=500, detail="Failed to update workflow record")


@router.post(
    "/delete-multiple",
    summary="Delete multiple workflow records by study_uid and task_name",
)
async def delete_multiple_workflows(
    data: WorkflowDeleteMultipleRequest, conn=Depends(get_db_connection)
):
    # Build the query to delete multiple records
    query = """
    DELETE FROM workflow_details
    WHERE (study_uid, task_name) IN (
        SELECT * FROM UNNEST($1::text[], $2::text[])
    )
    """
    try:
        # Split the input records into two lists
        study_uids = [record.study_uid for record in data.records]
        task_names = [record.task_name for record in data.records]

        # Execute the query
        result = await conn.execute(query, study_uids, task_names)
        deleted_count = result.split()[1] if result else "0"
        if deleted_count == "0":
            return {"message": "No matching workflow records found", "deleted": 0}
        return {"message": f"{deleted_count} workflow records deleted successfully", "deleted": int(deleted_count)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting workflow records: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete workflow records")


@router.post("/{task_name}/filter-uid", summary="Filter by task_name and retrieve only study_uid")
async def filter_study_uid_by_task_name(
    task_name: str = Path(..., description="Workflow Name to filter by."), conn=Depends(get_db_connection)
):
    try:
        query = "SELECT study_uid FROM workflow_details WHERE task_name = $1"
        records = await conn.fetch(query, task_name)
        return [record["study_uid"] for record in records]
    except Exception as e:
        logger.error(f"Filter error: {e}")
        raise HTTPException(status_code=500, detail="Filter operation failed")


@router.post(
    "/{task_name}/filter-annotated", summary="Filter by task_name and retrieve annotation status"
)
async def filter_series_id_by_task_name(
    task_name: str = Path(..., description="task name to filter by"), conn=Depends(get_db_connection)
):
    try:
        # Corrected query to fetch the 'annotated' field for multiple study_uids
        query = """
        SELECT study_uid, annotated
        FROM workflow_details
        WHERE task_name = $1
        """

        # Pass both task_name and the list of study_uids to the query
        records = await conn.fetch(query, task_name)

        # Convert the records to a dictionary with study_uid as the key and annotated status as the value
        result = {record["study_uid"]: record["annotated"] for record in records}

        return result

    except Exception as e:
        logger.error(f"Filter error: {e}")
        raise HTTPException(status_code=500, detail="Filter operation failed")


@router.post(
    "/{task_name}/filter",
    summary="Filter by task_name and retrieve study_uid, volume_series_instance_uid, label_series_instance_uid, and modality",
)
async def filter_workflow_data(
    task_name: str = Path(..., description="Task name to filter by"), conn=Depends(get_db_connection)
):
    try:
        query = """
        SELECT study_uid, volume_series_instance_uid, label_series_instance_uid, modality FROM workflow_details WHERE task_name = $1
        """
        records = await conn.fetch(query, task_name)
        return {
            "study_uid": [record["study_uid"] for record in records],
            "series_instance_uid": [
                record["volume_series_instance_uid"] for record in records
            ],
            "instance_uid": [record["label_series_instance_uid"] for record in records],
            "modality": [record["modality"] for record in records],
        }
    except Exception as e:
        logger.error(f"Filter error: {e}")
        raise HTTPException(status_code=500, detail="Filter operation failed")


@router.post("/add", summary="Add a new task to workflow")
async def add_task_to_workflow(data: ListWorkflowRequest, conn=Depends(get_db_connection)):
    query = """
        INSERT INTO workflow (task_name)
        VALUES ($1)
        """
    try:
        await conn.execute(query, data.task_name)
        return {"message": "Task added to workflow successfully"}
    except Exception as e:
        logger.error(f"Error adding task to workflow: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to add task to workflow"
        )


@router.get("/", summary="Fetch all records from _workflow")
async def get_all_workflow(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    conn=Depends(get_db_connection),
):
    try:
        # Query to fetch all rows from workflow
        query = "SELECT * FROM workflow ORDER BY task_name OFFSET $1 LIMIT $2"
        records = await conn.fetch(query, skip, limit)

        # Return the records as a list of dictionaries
        return [dict(record) for record in records]
    except Exception as e:
        logger.error(f"Error fetching workflow records: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve workflow records"
        )


@router.delete("/delete", summary="Delete a workflow")
async def delete_workflow(
    data: ListWorkflowRequest, conn=Depends(get_db_connection)
):
    query = "DELETE FROM workflow WHERE task_name = $1"
    try:
        result = await conn.execute(query, data.task_name)
        if result == "DELETE 0":
            raise HTTPException(
                status_code=404, detail="Task not found in workflow"
            )
        return {"message": "Task deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting task from workflow: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to delete task from workflow"
        )


@router.post("/{task_name}/update-train", summary="Update train flag for a workflow record")
async def update_train_flag(
    task_name: str = Path(..., description="Task name to filter records"),
    study_uid: str = Query(..., description="Study Instance UID to identify record"),
    train: bool = Query(..., description="New train flag value"),
    conn=Depends(get_db_connection),
):
    query = """
    UPDATE workflow_details
    SET train = $1
    WHERE task_name = $2 AND study_uid = $3
    """
    try:
        result = await conn.execute(query, train, task_name, study_uid)
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Workflow record not found")
        return {"message": "Train flag updated successfully"}
    except Exception as e:
        logger.error(f"Error updating train flag: {e}")
        raise HTTPException(status_code=500, detail="Failed to update train flag")


@router.get("/{task_name}/get-train", summary="Fetch train flag for a workflow record")
async def get_train_flag(
    task_name: str = Path(..., description="Task name to filter records"),
    study_uid: str = Query(..., description="Case ID to identify record"),
    conn=Depends(get_db_connection),
):
    query = """
    SELECT train FROM workflow_details WHERE task_name = $1 AND study_uid = $2
    """
    try:
        result = await conn.fetchval(query, task_name, study_uid)
        if result is None:
            raise HTTPException(status_code=404, detail="Workflow record not found")
        return {"train": result}
    except Exception as e:
        logger.error(f"Error fetching train flag: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch train flag")


@router.post(
    "/{task_name}/train",
    summary="Start nnUNet training for a specific workflow",
    status_code=status.HTTP_202_ACCEPTED,
)
async def initialize_training(
    task_name: str = Path(..., description="Task name to start training for"), conn=Depends(get_db_connection)
):
    try:
        query = """
        SELECT study_uid, volume_series_instance_uid, label_series_instance_uid, modality, train FROM workflow_details WHERE task_name = $1
        """
        records = await conn.fetch(query, task_name)
        if not records:
            raise HTTPException(status_code=404, detail="No records found for the given task")

        job_id = uuid4().hex
        payload = {
            "job_id": job_id,
            "task_name": task_name,
            "records": [dict(record) for record in records],
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
    except Exception as exc:
        logger.exception("Error starting nnUNet training for %s", task_name)
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
