from fastapi import HTTPException
from uuid import UUID, uuid4
 
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import SQLAlchemyError
 
from models.steps import PlanStep
from models.Taskmessage import TaskMessage
from models.contextaugmentation import ContextAugmentation
from schemas.steps import PlanStepCreate, PlanStepUpdate

def _step_query():
    """Base select with relationships always eager-loaded."""
    return (
        select(PlanStep)
        .options(
            selectinload(PlanStep.task_message).selectinload(
                TaskMessage.context_augmentation
            )
        )
    )
 
 
async def add_step_to_plan(db: AsyncSession, plan_id: UUID, step_in: PlanStepCreate) -> PlanStep:
    try:
        # Auto-increment order within this plan
        result = await db.execute(
            select(PlanStep.order)
            .where(PlanStep.plan_id == plan_id)
            .order_by(desc(PlanStep.order))
        )
        last_order = result.scalar()
        next_order = 1 if last_order is None else last_order + 1
 
        step_data = step_in.model_dump(exclude={"task_message"})
        new_step = PlanStep(id=uuid4(), plan_id=plan_id, order=next_order, **step_data)
        db.add(new_step)
        await db.flush()  # get new_step.id
 
        # Create nested TaskMessage + ContextAugmentation if provided
        if step_in.task_message is not None:
            ca = ContextAugmentation(
                id=uuid4(),
                **step_in.task_message.context_augmentation.model_dump()
            )
            db.add(ca)
            await db.flush()
 
            tm_data = step_in.task_message.model_dump(exclude={"context_augmentation"})
            tm = TaskMessage(
                id=uuid4(),
                task_id=str(uuid4()),        # auto-generated, never from caller
                agent_id=new_step.id,        # step IS the agent
                context_augmentation_id=ca.id,
                **tm_data,
            )
            db.add(tm)
 
        await db.commit()
        return await get_step(db, new_step.id)
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
 
 
async def get_step(db: AsyncSession, step_id: UUID) -> PlanStep:
    try:
        result = await db.execute(
            _step_query().where(PlanStep.id == step_id)
        )
        step = result.scalar_one_or_none()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")
        return step
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
 
 
async def get_steps_for_plan(db: AsyncSession, plan_id: UUID) -> list[PlanStep]:
    try:
        result = await db.execute(
            _step_query()
            .where(PlanStep.plan_id == plan_id)
            .order_by(PlanStep.order)
        )
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
 
 
async def get_all_steps(db: AsyncSession, skip: int = 0, limit: int = 100) -> list[PlanStep]:
    try:
        result = await db.execute(_step_query().offset(skip).limit(limit))
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
 
 
async def update_step(db: AsyncSession, step_id: UUID, step_in: PlanStepUpdate) -> PlanStep:
    step = await get_step(db, step_id)
    step_data = step_in.model_dump(exclude_unset=True, exclude={"task_message"})
 
    for field, value in step_data.items():
        setattr(step, field, value)
 
    # Handle nested task_message update
    if step_in.task_message is not None:
        if step.task_message:
            # Update existing TaskMessage
            tm_data = step_in.task_message.model_dump(
                exclude_unset=True, exclude={"context_augmentation"}
            )
            for field, value in tm_data.items():
                setattr(step.task_message, field, value)
 
            # Update nested ContextAugmentation if provided
            if (
                step_in.task_message.context_augmentation is not None
                and step.task_message.context_augmentation
            ):
                ca_data = step_in.task_message.context_augmentation.model_dump(exclude_unset=True)
                for field, value in ca_data.items():
                    setattr(step.task_message.context_augmentation, field, value)
        else:
            # No TaskMessage yet — create one now
            ca = ContextAugmentation(
                id=uuid4(),
                **step_in.task_message.context_augmentation.model_dump()
            )
            db.add(ca)
            await db.flush()
 
            tm_data = step_in.task_message.model_dump(exclude={"context_augmentation"})
            tm = TaskMessage(
                id=uuid4(),
                task_id=str(uuid4()),        # auto-generated, never from caller
                agent_id=step.id,
                context_augmentation_id=ca.id,
                **tm_data,
            )
            db.add(tm)
 
    try:
        await db.commit()
        return await get_step(db, step_id)
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
 
 
async def delete_step(db: AsyncSession, step_id: UUID) -> dict:
    step = await get_step(db, step_id)
    try:
        await db.delete(step)   # cascade handles TaskMessage + ContextAugmentation
        await db.commit()
        return {"detail": "Step deleted"}
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


from sqlalchemy import func

async def update_step_status(
    db: AsyncSession, step_id: UUID, status: str, log: dict | None = None
) -> PlanStep:
    step = await get_step(db, step_id)

    step.status = status

    if log is not None:
        # Append to the existing JSONB array safely
        current_logs = step.logs or []
        step.logs = current_logs + [log]
        # Tell SQLAlchemy the JSONB column mutated
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(step, "logs")

    try:
        await db.commit()
        return await get_step(db, step_id)
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")