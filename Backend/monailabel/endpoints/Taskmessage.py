# app/routers/task_message.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from core.database import get_db
from crud.Taskmessage import (
    create_task_message,
    get_task_message,
    get_task_message_by_step,
    update_task_message,
    update_task_message_by_step,
    delete_task_message,
)
from schemas.Taskmessage import TaskMessageCreate, TaskMessageUpdate, TaskMessageRead

router = APIRouter(prefix="/task-messages", tags=["Task Messages"])
 
 
@router.post("/step/{step_id}", response_model=TaskMessageRead, status_code=201)
async def create_for_step(
    step_id: UUID, data: TaskMessageCreate, db: AsyncSession = Depends(get_db)
):
    """
    Create a TaskMessage (+ ContextAugmentation) for an existing PlanStep.
    Returns 409 if this step already has a TaskMessage — use PATCH instead.
    """
    return await create_task_message(db, agent_id=step_id, data=data)
 
 
@router.get("/step/{step_id}", response_model=TaskMessageRead)
async def read_by_step(step_id: UUID, db: AsyncSession = Depends(get_db)):
    """Fetch the TaskMessage that belongs to a given PlanStep."""
    return await get_task_message_by_step(db, agent_id=step_id)
 
 
@router.patch("/step/{step_id}", response_model=TaskMessageRead)
async def update_by_step(
    step_id: UUID, data: TaskMessageUpdate, db: AsyncSession = Depends(get_db)
):
    """Update the TaskMessage for a given PlanStep (by step id)."""
    return await update_task_message_by_step(db, agent_id=step_id, data=data)
 
 
@router.get("/{tm_id}", response_model=TaskMessageRead)
async def read(tm_id: UUID, db: AsyncSession = Depends(get_db)):
    return await get_task_message(db, tm_id)
 
 
@router.patch("/{tm_id}", response_model=TaskMessageRead)
async def update(tm_id: UUID, data: TaskMessageUpdate, db: AsyncSession = Depends(get_db)):
    return await update_task_message(db, tm_id, data)
 
 
@router.delete("/{tm_id}")
async def delete(tm_id: UUID, db: AsyncSession = Depends(get_db)):
    return await delete_task_message(db, tm_id)