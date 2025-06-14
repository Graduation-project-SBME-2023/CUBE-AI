# README: Data Management with PostgreSQL, Monai Label, and OHIF

## Step 1: Install PostgreSQL

1. Download PostgreSQL from [this link](https://www.postgresql.org/download/).
2. Install PostgreSQL and ensure to note down the database password during setup. During the installation process, you will be prompted to set up the localhost connection parameters (default is 5432). Make sure to remember these settings as well.

## Step 2: Set Up the Database

1. Open **pgAdmin**.
2. Create a new database named `work_flow`.
3. Navigate to the **Schemas** section, right-click on it, and select **Query Tool**.

4. Run the following SQL query to create the `list_workflow` table:
```sql
   CREATE TABLE IF NOT EXISTS list_workflow (
    task_name TEXT PRIMARY KEY
);
   ```
5. Run the following SQL query to create the `workflow` table:
   ```sql
    CREATE TABLE IF NOT EXISTS public.workflow
    (
        case_id text COLLATE pg_catalog."default",
        task_name text COLLATE pg_catalog."default",
        series_instance_uid text COLLATE pg_catalog."default",
        instance_uid text COLLATE pg_catalog."default",
        annotated boolean DEFAULT false,
        train boolean,
        modality text COLLATE pg_catalog."default",
        CONSTRAINT fk_task_name FOREIGN KEY (task_name)
            REFERENCES public.list_workflow (task_name) MATCH SIMPLE
            ON UPDATE NO ACTION
            ON DELETE CASCADE
    )

    TABLESPACE pg_default;

    ALTER TABLE IF EXISTS public.workflow
        OWNER to postgres;
   ```

### Additional Database Commands

- **View table data:**
  ```sql
  SELECT * FROM workflow;
  ```
- **Delete all rows:**
  ```sql
  DELETE FROM workflow;
  ```

- **Enforce unique task_name in list_workflow table**
```sql
    ALTER TABLE list_workflow
    ADD CONSTRAINT unique_task_name UNIQUE (task_name);
```

## Step 3: Add API in Monai Label Server

1. Navigate to the path of your Monai Label installation, typically:

   ```
    C:\Users\"your user name"\AppData\Local\Programs\Python\Python39\Lib\site-packages\monailabel   ```
   ```

   Replace `Python39` with your Python version.

2. Create a new file named `workflow.py` in the `endpoints` directory.

3. Add the following code to `workflow.py`:
 -  **Note : you have to install asyncpg accroding to following command**
   ```python
   pip install asyncpg
   ```
   ```python
   from fastapi import APIRouter, HTTPException, Depends ,Query
    from pydantic import BaseModel
    import asyncpg
    import logging
    from typing import Optional, List 
    import json


    logger = logging.getLogger(__name__)

    # Define router for workflow operations
    router = APIRouter(
        prefix="/workflow",
        tags=["Workflow"],
        responses={404: {"description": "Not found"}},
    )

    # Database connection settings
    DATABASE_URL = "postgresql://postgres:140921@localhost:5432/work_flow" #username-->postgres //140921 -->password of database//5432-->localhost//work_flow-->Database name


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
        """
        Creates a new workflow record after verifying the task exists in list_workflow.
        Requires:
        - case_id: Unique identifier for the medical case
        - task_name: Pre-existing workflow task name
        - series_instance_uid: DICOM series identifier
        - instance_uid: (Optional) DICOM instance identifier
        - train: (Optional) Flag for training data
        Sets 'annotated' automatically based on instance_uid presence.
    """
        async with conn.transaction():
            # Step 1: Ensure the task_name exists in list_workflow
            task_name_exists = await conn.fetchval("""
                SELECT EXISTS(SELECT 1 FROM list_workflow WHERE task_name = $1)
            """, data.task_name)

            if not task_name_exists:
                raise HTTPException(status_code=400, detail=f"Task name '{data.task_name}' does not exist in list_workflow.")
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
                    data.train if data.train else False
                )
                return {"message": "Workflow record added successfully"}
            except Exception as e:
                logger.error(f"Error adding workflow record: {e}")
                raise HTTPException(status_code=500, detail="Failed to add workflow record")


    @router.post("/update-uids", summary="Update data of a workflow record")
    async def update_workflow_uids(data: WorkflowUpdateUIDRequest, conn=Depends(get_db_connection)):
        """
        Updates DICOM identifiers and/or modality for an existing workflow record.
        Accepts any combination of:
        - series_instance_uid: Update series-level identifier
        - instance_uid: Update instance-level identifier
        - modality: Update imaging modality type
        Automatically sets 'annotated' to True when called.
        Requires existing case_id and task_name for update targeting.
    """
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


    @router.post("/delete-multiple", summary="Delete multiple workflow records by case_id and task_name")
    async def delete_multiple_workflows(data: WorkflowDeleteMultipleRequest, conn=Depends(get_db_connection)):
        """
        Deletes multiple workflow entries in a single operation.
        Input: List of objects containing case_id and task_name pairs.
        Useful for cleaning up related records efficiently.
    """
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
                raise HTTPException(status_code=404, detail="No matching workflow records found")
            return {"message": f"{result.split()[1]} workflow records deleted successfully"}
        except Exception as e:
            logger.error(f"Error deleting workflow records: {e}")
            raise HTTPException(status_code=500, detail="Failed to delete workflow records")


    @router.post("/filter", summary="Filter by task_name and retrieve only case_id")
    async def filter_case_id_by_task_name(
        filter: TaskNameFilterRequest,
        conn=Depends(get_db_connection)
    ):
        """
        Returns all case_ids associated with a specific workflow task.
        Helpful for getting all cases participating in a particular workflow.
        Output: List of case_id strings.
        """
        try:
            query = "SELECT case_id FROM workflow WHERE task_name = $1"
            records = await conn.fetch(query, filter.task_name)
            return [record["case_id"] for record in records]
        except Exception as e:
            logger.error(f"Filter error: {e}")
            raise HTTPException(status_code=500, detail="Filter operation failed")
        finally:
            await conn.close()


    @router.post("/filter-annotated", summary="Filter by task_name and retrieve annotation status")
    async def filter_series_id_by_task_name(
        filter: AnnotationFilterRequest,
        conn=Depends(get_db_connection)
    ):
        """
        Provides annotation completion status for all cases in a workflow task.
        Output: Dictionary with case_ids as keys and boolean annotated status as values.
        Helps track annotation progress across multiple cases.
        """
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


    @router.post("/filter-workflow", summary="Filter by task_name and retrieve case_id, series_instance_uid, instance_uid, and modality")
    async def filter_workflow_data(
        filter: TaskNameFilterRequest,
        conn=Depends(get_db_connection)
    ):
        """
        Retrieves complete imaging identifiers for cases in a workflow task.
        Returns:
        - case_ids: List of case identifiers
        - series_instance_uids: List of corresponding series IDs
        - instance_uids: List of instance-level IDs
        - modalities: List of imaging modalities used
        Essential for DICOM processing pipelines.
        """
        try:
            query = """
            SELECT case_id, series_instance_uid, instance_uid, modality FROM workflow WHERE task_name = $1
            """
            records = await conn.fetch(query, filter.task_name)
            return {
                "case_id": [record["case_id"] for record in records],
                "series_instance_uid": [record["series_instance_uid"] for record in records],
                "instance_uid": [record["instance_uid"] for record in records],
                "modality": [record["modality"] for record in records]
            }
        except Exception as e:
            logger.error(f"Filter error: {e}")
            raise HTTPException(status_code=500, detail="Filter operation failed")
        finally:
            await conn.close()

    @router.post("/list_workflow/add", summary="Add a new task to list_workflow")
    async def add_list_workflow(data: ListWorkflowRequest, conn=Depends(get_db_connection)):
        """
        Creates new workflow type in the system.
        Task names must be unique - ignores duplicate entries.
        Prerequisite for creating workflow records of this type.
        """
        query = """
            INSERT INTO list_workflow (task_name)
            VALUES ($1)
            ON CONFLICT (task_name) DO NOTHING
            """
        try:
            await conn.execute(query, data.task_name)
            return {"message": "Task added to list_workflow successfully"}
        except Exception as e:
            logger.error(f"Error adding task to list_workflow: {e}")
            raise HTTPException(status_code=500, detail="Failed to add task to list_workflow")

    @router.get("/list_workflow", summary="Fetch all records from list_workflow")
    async def get_all_list_workflow(conn=Depends(get_db_connection)):
        """
        Retrieves all registered workflow types in the system.
        Output: List of workflow task configurations.
        Shows available workflow types for selection.
        """
        try:
            # Query to fetch all rows from list_workflow
            query = "SELECT * FROM list_workflow"
            records = await conn.fetch(query)

            # Return the records as a list of dictionaries
            return [dict(record) for record in records]
        except Exception as e:
            logger.error(f"Error fetching list_workflow records: {e}")
            raise HTTPException(status_code=500, detail="Failed to retrieve list_workflow records")


    @router.delete("/list_workflow/delete", summary="Delete a task from list_workflow")
    async def delete_list_workflow(data: ListWorkflowRequest, conn=Depends(get_db_connection)):
         """
        Removes a workflow type from the system.
        Requires: Existing task_name.
        Note: Deleting a workflow type doesn't affect existing records.
        """
        query = "DELETE FROM list_workflow WHERE task_name = $1"
        try:
            result = await conn.execute(query, data.task_name)
            if result == "DELETE 0":
                raise HTTPException(status_code=404, detail="Task not found in list_workflow")
            return {"message": "Task deleted successfully"}
        except Exception as e:
            logger.error(f"Error deleting task from list_workflow: {e}")
            raise HTTPException(status_code=500, detail="Failed to delete task from list_workflow")

    @router.post("/update-train", summary="Update train flag for a workflow record")
    async def update_train_flag(
        task_name: str = Query(..., description="Task name to filter records"),
        case_id: str = Query(..., description="Study Instance UID to identify record"),
        train: bool = Query(..., description="New train flag value"),
        conn=Depends(get_db_connection)
    ):
        """
        Marks/unmarks a case as training data for machine learning.
        Input: Boolean flag (True=include in training)
        Affects data distribution in ML pipelines.
        """
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
        conn=Depends(get_db_connection)
    ):
        """
        Retrieves current training status of a case.
        Output: Boolean indicating if used for training.
        Helps manage ML dataset composition.
        """
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

   ```

4. In the `app.py` import `workflow` file and then add this line of code
```python
app.include_router(workflow.router, prefix=settings.MONAI_LABEL_API_STR)
```

## Some Limitations for the Database
**in workflow table**
1. Each `instance_uid` must be unique.

2. The same `series_instance_uid` cannot be used twice with the same `task_name`.

3. `case_id` can be repeated.

4. The `annotated` flag is set to `true` when `instance_uid` exists and `false` when it does not.

**in list_workflow table**

1. Each `task_name` must be unique.

2. Remove any row(`task_name`) from the List_workflow table will remove all records in workflow table with the same `task_name`
