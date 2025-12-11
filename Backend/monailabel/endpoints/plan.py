# app/routers/async_plan.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import List

from core.database import get_db
from crud.plan import (
    create_plan,
    get_plan,
    get_all_plans,
    update_plan,
    delete_plan,
    get_plans_for_hypothesis
)
from schemas.plan import PlanCreate, PlanUpdate, PlanOut, PlanWithStepsOut
from crud.steps import get_steps_for_plan

router = APIRouter(prefix="/plans", tags=["Plans"])

@router.post("/", response_model=PlanOut)
async def create_plan_endpoint(plan_in: PlanCreate, hypothesis_id: UUID, db: AsyncSession = Depends(get_db)):
    return await create_plan(db, hypothesis_id, plan_in)

@router.get("/", response_model=List[PlanOut])
async def list_plans(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    return await get_all_plans(db, skip, limit)

@router.get("/{plan_id}", response_model=PlanOut)
async def read_plan(plan_id: UUID, db: AsyncSession = Depends(get_db)):
    return await get_plan(db, plan_id)

@router.get("/{plan_id}/with_steps", response_model=PlanWithStepsOut)
async def read_plan_with_steps(plan_id: UUID, db: AsyncSession = Depends(get_db)):
    plan = await get_plan(db, plan_id)
    steps = await get_steps_for_plan(db, plan_id)
    return PlanWithStepsOut(
        id=plan.id,
        hypothesis_id=plan.hypothesis_id,
        study_goal=plan.study_goal,
        state=plan.state,
        steps=steps
    )

@router.get("/by-hypothesis/{hypothesis_id}", response_model=PlanWithStepsOut)
async def read_plan_by_hypothesis(hypothesis_id: UUID, db: AsyncSession = Depends(get_db)):
    return await get_plans_for_hypothesis(db, hypothesis_id)

@router.patch("/{plan_id}", response_model=PlanOut)
async def edit_plan(plan_id: UUID, plan_in: PlanUpdate, db: AsyncSession = Depends(get_db)):
    return await update_plan(db, plan_id, plan_in)

@router.delete("/{plan_id}")
async def remove_plan(plan_id: UUID, db: AsyncSession = Depends(get_db)):
    return await delete_plan(db, plan_id)
