# app/crud/task_message.py
from fastapi import HTTPException
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import SQLAlchemyError

from models.Taskmessage import TaskMessage
from models.contextaugmentation import ContextAugmentation
from schemas.Taskmessage import TaskMessageCreate, TaskMessageUpdate


async def create_task_message(
    db: AsyncSession, agent_id: UUID, data: TaskMessageCreate
) -> TaskMessage:
    """
    Creates a TaskMessage for a given PlanStep (agent_id == plan_step.id).
    Also creates the nested ContextAugmentation in the same transaction.
    Raises 409 if a TaskMessage already exists for this step.
    """
    # Guard: one TaskMessage per step — fail fast with a clear 409
    existing_result = await db.execute(
        select(TaskMessage).where(TaskMessage.agent_id == agent_id)
    )
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"A TaskMessage already exists for step {agent_id}. Use PATCH /task-messages/step/{agent_id} to update it."
        )
 
    try:
        # 1. Create ContextAugmentation first
        ca = ContextAugmentation(
            id=uuid4(),
            **data.context_augmentation.model_dump()
        )
        db.add(ca)
        await db.flush()  # get ca.id without committing
 
        # 2. Create TaskMessage pointing at the step and the context
        task_data = data.model_dump(exclude={"context_augmentation"})
        tm = TaskMessage(
            id=uuid4(),
            task_id=str(uuid4()),    # auto-generated, never from caller
            agent_id=agent_id,
            context_augmentation_id=ca.id,
            **task_data,
        )
        db.add(tm)
        await db.commit()
        await db.refresh(tm)
 
        return await get_task_message(db, tm.id)   # return with relationships loaded
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
 
 
async def get_task_message(db: AsyncSession, tm_id: UUID) -> TaskMessage:
    try:
        result = await db.execute(
            select(TaskMessage)
            .options(selectinload(TaskMessage.context_augmentation))
            .where(TaskMessage.id == tm_id)
        )
        obj = result.scalar_one_or_none()
        if not obj:
            raise HTTPException(status_code=404, detail="TaskMessage not found")
        return obj
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
 
 
async def get_task_message_by_step(db: AsyncSession, agent_id: UUID) -> TaskMessage:
    """Fetch the TaskMessage that belongs to a given PlanStep."""
    try:
        result = await db.execute(
            select(TaskMessage)
            .options(selectinload(TaskMessage.context_augmentation))
            .where(TaskMessage.agent_id == agent_id)
        )
        obj = result.scalar_one_or_none()
        if not obj:
            raise HTTPException(status_code=404, detail="TaskMessage not found for this step")
        return obj
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def get_task_message_by_task_id(db: AsyncSession, task_id: str) -> TaskMessage:
    """Fetch a TaskMessage by its task_id field."""
    try:
        result = await db.execute(
            select(TaskMessage)
            .options(selectinload(TaskMessage.context_augmentation))
            .where(TaskMessage.task_id == task_id)
        )
        obj = result.scalar_one_or_none()
        if not obj:
            raise HTTPException(status_code=404, detail="TaskMessage not found for this task_id")
        return obj
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
 
 
async def update_task_message_by_step(
    db: AsyncSession, agent_id: UUID, data: TaskMessageUpdate
) -> TaskMessage:
    """Update the TaskMessage that belongs to a given PlanStep (by step id)."""
    obj = await get_task_message_by_step(db, agent_id)
    return await _apply_task_message_update(db, obj, data)
 
 
async def update_task_message(
    db: AsyncSession, tm_id: UUID, data: TaskMessageUpdate
) -> TaskMessage:
    """Update a TaskMessage by its own id."""
    obj = await get_task_message(db, tm_id)
    return await _apply_task_message_update(db, obj, data)
 
 
async def _apply_task_message_update(
    db: AsyncSession, obj: TaskMessage, data: TaskMessageUpdate
) -> TaskMessage:
    update_data = data.model_dump(exclude_unset=True, exclude={"context_augmentation"})
    for field, value in update_data.items():
        setattr(obj, field, value)
 
    # Update nested ContextAugmentation if provided
    if data.context_augmentation is not None and obj.context_augmentation_id:
        ca_result = await db.execute(
            select(ContextAugmentation).where(
                ContextAugmentation.id == obj.context_augmentation_id
            )
        )
        ca = ca_result.scalar_one_or_none()
        if ca:
            for field, value in data.context_augmentation.model_dump(exclude_unset=True).items():
                setattr(ca, field, value)
 
    try:
        await db.commit()
        return await get_task_message(db, obj.id)
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
 
 
async def delete_task_message(db: AsyncSession, tm_id: UUID) -> dict:
    obj = await get_task_message(db, tm_id)
    try:
        await db.delete(obj)
        await db.commit()
        return {"detail": "TaskMessage deleted"}
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")