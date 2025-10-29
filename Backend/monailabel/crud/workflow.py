from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from models.workflow import Workflow
from schemas.workflow import WorkflowCreate

# CREATE
async def create_workflow(db: AsyncSession, data: WorkflowCreate):
    db_workflow = Workflow(**data.dict())
    db.add(db_workflow)
    await db.commit()
    await db.refresh(db_workflow)
    return db_workflow


# GET ONE
async def get_workflow(db: AsyncSession, task_name: str):
    result = await db.execute(
        select(Workflow).where(Workflow.task_name == task_name)
    )
    return result.scalar_one_or_none()


# GET ALL
async def get_workflows(db: AsyncSession, skip: int = 0, limit: int = 100):
    result = await db.execute(select(Workflow).offset(skip).limit(limit))
    return result.scalars().all()


# DELETE
async def delete_workflow(db: AsyncSession, task_name: str):
    result = await db.execute(
        select(Workflow).where(Workflow.task_name == task_name)
    )
    workflow = result.scalar_one_or_none()
    if not workflow:
        return False

    await db.delete(workflow)
    await db.commit()
    return True
