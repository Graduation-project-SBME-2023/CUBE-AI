from sqlalchemy.orm import Session
from models.workflow_details import WorkflowDetail
from schemas.workflow_details import WorkflowDetailCreate, WorkflowDetailUpdate
from typing import List, Optional

# ---------------------------------------------------------
# Retrieve all workflow detail entries
# ---------------------------------------------------------
def get_all_workflow_details(db: Session) -> List[WorkflowDetail]:
    """
    Fetch all records from the workflow_details table.
    Returns a list of WorkflowDetail ORM objects.
    """
    return db.query(WorkflowDetail).all()


# ---------------------------------------------------------
# Retrieve a specific workflow detail by composite keys
# ---------------------------------------------------------
def get_workflow_detail(
    db: Session,
    task_name: str,
    study_UID: str,
    volume_series_instance_UID: str,
) -> Optional[WorkflowDetail]:
    """
    Fetch a single WorkflowDetail using its composite primary keys:
    - task_name
    - study_UID
    - volume_series_instance_UID
    Returns the WorkflowDetail ORM object if found, otherwise None.
    """
    return (
        db.query(WorkflowDetail)
        .filter_by(
            task_name=task_name,
            study_UID=study_UID,
            volume_series_instance_UID=volume_series_instance_UID,
        )
        .first()
    )


# ---------------------------------------------------------
# Create a new workflow detail record
# ---------------------------------------------------------
def create_workflow_detail(db: Session, detail: WorkflowDetailCreate) -> WorkflowDetail:
    """
    Create a new WorkflowDetail entry using the data provided
    in the WorkflowDetailCreate schema.
    """
    # Convert Pydantic model into dictionary and unpack it into ORM model
    db_detail = WorkflowDetail(**detail.dict())
    db.add(db_detail)
    db.commit()          # Save to the database
    db.refresh(db_detail)  # Refresh instance with DB-generated defaults
    return db_detail


# ---------------------------------------------------------
# Update an existing workflow detail record
# ---------------------------------------------------------
def update_workflow_detail(
    db: Session,
    task_name: str,
    study_UID: str,
    volume_series_instance_UID: str,
    detail_update: WorkflowDetailUpdate,
) -> Optional[WorkflowDetail]:
    """
    Update an existing WorkflowDetail entry identified by its
    composite keys. Only fields provided in the update schema
    (exclude_unset=True) will be updated.
    Returns the updated WorkflowDetail or None if not found.
    """
    # Fetch the record first
    db_detail = get_workflow_detail(db, task_name, study_UID, volume_series_instance_UID)
    if not db_detail:
        return None

    # Only update fields provided in the request
    update_data = detail_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_detail, key, value)

    db.commit()
    db.refresh(db_detail)
    return db_detail


# ---------------------------------------------------------
# Delete a workflow detail record
# ---------------------------------------------------------
def delete_workflow_detail(
    db: Session,
    task_name: str,
    study_UID: str,
    volume_series_instance_UID: str,
) -> bool:
    """
    Delete a WorkflowDetail entry identified by composite keys.
    Returns True if deletion succeeded, or False if record not found.
    """
    db_detail = get_workflow_detail(db, task_name, study_UID, volume_series_instance_UID)
    if not db_detail:
        return False

    db.delete(db_detail)
    db.commit()
    return True