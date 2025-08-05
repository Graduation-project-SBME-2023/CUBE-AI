from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
import asyncpg
import logging
from typing import Optional, List
import os


logger = logging.getLogger(__name__)

# Define router for workflow operations
router = APIRouter(
    prefix="/test-workflow",
    tags=["Test Workflow"],
    responses={404: {"description": "Not found"}},
)

# Database connection settings
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://workflow_user:password@postgres:5432/workflow_db")


# Define the Pydantic model for validation
class WorkflowRequest(BaseModel):
    case_id: str
    task_name: str
    series_instance_uid: str
    instance_uid: Optional[str] = None
    train: Optional[bool]


# New Pydantic model for updating series_instance_uid and instance_uid
class WorkflowUpdateUIDRequest(BaseModel):
    case_id: str
    task_name: str
    series_instance_uid: Optional[str] = None
    instance_uid: Optional[str] = None
    modality: Optional[str] = None


class WorkflowDeleteRequest(BaseModel):
    case_id: str
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


# Database Connection Pool
async def get_db_connection():
    return await asyncpg.connect(DATABASE_URL)


@router.post("/add", summary="Add a new workflow record")
async def add_workflow(data: WorkflowRequest, conn=Depends(get_db_connection)):
    async with conn.transaction():
        # Step 1: Ensure the task_name exists in list_workflow
        task_name_exists = await conn.fetchval(
            """
            SELECT EXISTS(SELECT 1 FROM list_workflow WHERE task_name = $1)
        """,
            data.task_name,
        )

        if not task_name_exists:
            raise HTTPException(
                status_code=400,
                detail=f"Task name '{data.task_name}' does not exist in list_workflow.",
            )

        # Step 2: Insert into workflow
        query = """
        INSERT INTO workflow (case_id, task_name, series_instance_uid, instance_uid, annotated, train)
        VALUES ($1, $2, $3, $4, $5, $6)
        """
        try:
            await conn.execute(
                query,
                data.case_id,
                data.task_name,
                data.series_instance_uid,
                data.instance_uid if data.instance_uid else None,
                True if data.instance_uid else False,
                data.train if data.train else False,
            )
            return {"message": "Workflow record added successfully"}
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
        update_fields.append("series_instance_uid = $1")
        params.append(data.series_instance_uid)
    if data.instance_uid is not None:
        update_fields.append("instance_uid = $2")
        params.append(data.instance_uid)
    if data.modality is not None:
        update_fields.append("modality = $3")
        params.append(data.modality)

    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    update_fields.append("annotated = TRUE")

    # Add case_id to params for the WHERE clause
    params.append(data.case_id)
    params.append(data.task_name)

    query = f"""
    UPDATE workflow
    SET {', '.join(update_fields)}
    WHERE case_id = ${len(params)-1} AND task_name = ${len(params)}
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
    summary="Delete multiple workflow records by case_id and task_name",
)
async def delete_multiple_workflows(
    data: WorkflowDeleteMultipleRequest, conn=Depends(get_db_connection)
):
    # Build the query to delete multiple records
    query = """
    DELETE FROM workflow
    WHERE (case_id, task_name) IN (
        SELECT * FROM UNNEST($1::text[], $2::text[])
    )
    """
    try:
        # Split the input records into two lists
        case_ids = [record.case_id for record in data.records]
        task_names = [record.task_name for record in data.records]

        # Execute the query
        result = await conn.execute(query, case_ids, task_names)
        if result == "DELETE 0":
            raise HTTPException(
                status_code=404, detail="No matching workflow records found"
            )
        return {"message": f"{result.split()[1]} workflow records deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting workflow records: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete workflow records")


@router.post("/filter", summary="Filter by task_name and retrieve only case_id")
async def filter_case_id_by_task_name(
    filter: TaskNameFilterRequest, conn=Depends(get_db_connection)
):
    try:
        query = "SELECT case_id FROM workflow WHERE task_name = $1"
        records = await conn.fetch(query, filter.task_name)
        return [record["case_id"] for record in records]
    except Exception as e:
        logger.error(f"Filter error: {e}")
        raise HTTPException(status_code=500, detail="Filter operation failed")
    finally:
        await conn.close()


@router.post(
    "/filter-annotated", summary="Filter by task_name and retrieve annotation status"
)
async def filter_series_id_by_task_name(
    filter: AnnotationFilterRequest, conn=Depends(get_db_connection)
):
    try:
        # Corrected query to fetch the 'annotated' field for multiple case_ids
        query = """
        SELECT case_id, annotated
        FROM workflow
        WHERE task_name = $1
        """

        # Pass both task_name and the list of case_ids to the query
        records = await conn.fetch(query, filter.task_name)

        # Convert the records to a dictionary with case_id as the key and annotated status as the value
        result = {record["case_id"]: record["annotated"] for record in records}

        return result

    except Exception as e:
        logger.error(f"Filter error: {e}")
        raise HTTPException(status_code=500, detail="Filter operation failed")


@router.post(
    "/filter-workflow",
    summary="Filter by task_name and retrieve case_id, series_instance_uid, instance_uid, and modality",
)
async def filter_workflow_data(
    filter: TaskNameFilterRequest, conn=Depends(get_db_connection)
):
    try:
        query = """
        SELECT case_id, series_instance_uid, instance_uid, modality FROM workflow WHERE task_name = $1
        """
        records = await conn.fetch(query, filter.task_name)
        return {
            "case_id": [record["case_id"] for record in records],
            "series_instance_uid": [
                record["series_instance_uid"] for record in records
            ],
            "instance_uid": [record["instance_uid"] for record in records],
            "modality": [record["modality"] for record in records],
        }
    except Exception as e:
        logger.error(f"Filter error: {e}")
        raise HTTPException(status_code=500, detail="Filter operation failed")
    finally:
        await conn.close()


@router.post("/list_workflow/add", summary="Add a new task to list_workflow")
async def add_list_workflow(data: ListWorkflowRequest, conn=Depends(get_db_connection)):
    query = """
        INSERT INTO list_workflow (task_name)
        VALUES ($1)
        """
    try:
        await conn.execute(query, data.task_name)
        return {"message": "Task added to list_workflow successfully"}
    except Exception as e:
        logger.error(f"Error adding task to list_workflow: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to add task to list_workflow"
        )


@router.get("/list_workflow", summary="Fetch all records from list_workflow")
async def get_all_list_workflow(conn=Depends(get_db_connection)):
    try:
        # Query to fetch all rows from list_workflow
        query = "SELECT * FROM list_workflow"
        records = await conn.fetch(query)

        # Return the records as a list of dictionaries
        return [dict(record) for record in records]
    except Exception as e:
        logger.error(f"Error fetching list_workflow records: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve list_workflow records"
        )


@router.delete("/list_workflow/delete", summary="Delete a task from list_workflow")
async def delete_list_workflow(
    data: ListWorkflowRequest, conn=Depends(get_db_connection)
):
    query = "DELETE FROM list_workflow WHERE task_name = $1"
    try:
        result = await conn.execute(query, data.task_name)
        if result == "DELETE 0":
            raise HTTPException(
                status_code=404, detail="Task not found in list_workflow"
            )
        return {"message": "Task deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting task from list_workflow: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to delete task from list_workflow"
        )


@router.post("/update-train", summary="Update train flag for a workflow record")
async def update_train_flag(
    task_name: str = Query(..., description="Task name to filter records"),
    case_id: str = Query(..., description="Study Instance UID to identify record"),
    train: bool = Query(..., description="New train flag value"),
    conn=Depends(get_db_connection),
):
    query = """
    UPDATE workflow
    SET train = $1
    WHERE task_name = $2 AND case_id = $3
    """
    try:
        result = await conn.execute(query, train, task_name, case_id)
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Workflow record not found")
        return {"message": "Train flag updated successfully"}
    except Exception as e:
        logger.error(f"Error updating train flag: {e}")
        raise HTTPException(status_code=500, detail="Failed to update train flag")


@router.get("/get-train", summary="Fetch train flag for a workflow record")
async def get_train_flag(
    task_name: str = Query(..., description="Task name to filter records"),
    case_id: str = Query(..., description="Case ID to identify record"),
    conn=Depends(get_db_connection),
):
    query = """
    SELECT train FROM workflow WHERE task_name = $1 AND case_id = $2
    """
    try:
        result = await conn.fetchval(query, task_name, case_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Workflow record not found")
        return {"train": result}
    except Exception as e:
        logger.error(f"Error fetching train flag: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch train flag")
