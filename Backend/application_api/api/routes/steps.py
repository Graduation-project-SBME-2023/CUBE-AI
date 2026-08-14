# 
# app/routers/plan_step.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import List

from application_api.repositories.steps import update_step_status
from application_api.schemas.steps import PlanStepStatusUpdate
from application_api.db.session import get_db
from application_api.repositories.steps import (
    add_step_to_plan,
    get_step,
    get_steps_for_plan,
    get_all_steps,
    update_step,
    delete_step,
)
from application_api.schemas.steps import PlanStepCreate, PlanStepUpdate, PlanStepOut

router = APIRouter(prefix="/steps", tags=["Plan Steps"])


@router.post("/plan/{plan_id}", response_model=PlanStepOut, status_code=201)
async def create_step(
    plan_id: UUID, step_in: PlanStepCreate, db: AsyncSession = Depends(get_db)
):
    """
    Create a step for a plan.
    Optionally include a `task_message` body to simultaneously create
    the TaskMessage + ContextAugmentation for this step.
    """
    return await add_step_to_plan(db, plan_id, step_in)


@router.get("/", response_model=List[PlanStepOut])
async def list_steps(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await get_all_steps(db, skip, limit)


@router.get("/plan/{plan_id}", response_model=List[PlanStepOut])
async def list_steps_for_plan(
    plan_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await get_steps_for_plan(db, plan_id, skip=skip, limit=limit)


@router.get("/{step_id}", response_model=PlanStepOut)
async def read_step(step_id: UUID, db: AsyncSession = Depends(get_db)):
    return await get_step(db, step_id)


@router.patch("/{step_id}", response_model=PlanStepOut)
async def edit_step(
    step_id: UUID, step_in: PlanStepUpdate, db: AsyncSession = Depends(get_db)
):
    """
    Update a step. Include `task_message` in the body to:
    - Update the existing TaskMessage + ContextAugmentation, or
    - Create one if the step didn't have one yet.
    """
    return await update_step(db, step_id, step_in)


@router.patch("/{step_id}/status", response_model=PlanStepOut)
async def update_step_status_endpoint(
    step_id: UUID,
    status_in: PlanStepStatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Update a step's status and optionally append a log entry.

    """
    return await update_step_status(db, step_id, status_in.status, status_in.log)

@router.delete("/{step_id}")
async def remove_step(step_id: UUID, db: AsyncSession = Depends(get_db)):
    """Deletes step and cascades to TaskMessage + ContextAugmentation."""
    return await delete_step(db, step_id)
