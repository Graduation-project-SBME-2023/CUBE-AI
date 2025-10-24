from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from monailabel import models, schemas

async def create_workflow(db: AsyncSession, data: schemas.WorkflowCreate):
    db_workflow = models.Workflow(
        **data.dict()
    )
    db.add(db_workflow)
    await db.commit()
    await db.refresh(db_workflow)
    return db_workflow
    result = await db.execute(
        select(models.Workflow).where(
            models.Workflow.case_id == case_id,
            models.Workflow.task_name == task_name
        )
    )
    workflow = result.scalar_one_or_none()
    if not workflow:
        return None

    for field, value in data.dict(exclude_unset=True).items():
        setattr(workflow, field, value)
    if data.instance_uid is not None:
        workflow.annotated = True

    await db.commit()
    await db.refresh(workflow)
    return workflow

async def delete_workflow(db: AsyncSession, task_name: str):
    result = await db.execute(
        select(models.Workflow).where(
            models.Workflow.task_name == task_name
        )
    )
    workflow = result.scalar_one_or_none()
    if not workflow:
        return False

    await db.delete(workflow)
    await db.commit()
    return True