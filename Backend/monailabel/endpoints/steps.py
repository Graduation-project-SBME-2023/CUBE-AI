# app/routers/async_plan_step.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import List

from core.database import get_db
from crud.steps import (
    add_step_to_plan,
    get_step,
    get_steps_for_plan,
    get_all_steps,
    update_step,
    delete_step
)
from schemas.steps import PlanStepCreate, PlanStepUpdate, PlanStepOut

router = APIRouter(prefix="/steps", tags=["Plan Steps"])

@router.post("/plan/{plan_id}", response_model=PlanStepOut)
async def create_step(plan_id: UUID, step_in: PlanStepCreate, db: AsyncSession = Depends(get_db)):
    return await add_step_to_plan(db, plan_id, step_in)

@router.get("/", response_model=List[PlanStepOut])
async def list_steps(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    return await get_all_steps(db, skip, limit)

@router.get("/plan/{plan_id}", response_model=List[PlanStepOut])
async def list_steps_for_plan(plan_id: UUID, db: AsyncSession = Depends(get_db)):
    return await get_steps_for_plan(db, plan_id)

@router.get("/{step_id}", response_model=PlanStepOut)
async def read_step(step_id: UUID, db: AsyncSession = Depends(get_db)):
    return await get_step(db, step_id)

@router.patch("/{step_id}", response_model=PlanStepOut)
async def edit_step(step_id: UUID, step_in: PlanStepUpdate, db: AsyncSession = Depends(get_db)):
    return await update_step(db, step_id, step_in)

@router.delete("/{step_id}")
async def remove_step(step_id: UUID, db: AsyncSession = Depends(get_db)):
    return await delete_step(db, step_id)
