# app/routers/async_plan.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID
from typing import List
from sqlalchemy.orm import selectinload

from schemas.steps import PlanStepOut
from models.steps import PlanStep
from models.Taskmessage import TaskMessage
from models.contextaugmentation import ContextAugmentation
from core.database import get_db
from crud.plan import (
    create_plan,
    get_plan,
    get_all_plans,
    update_plan,
    delete_plan,
    get_plans_for_hypothesis,
    create_plan_with_steps,
    update_plan_with_steps,
)
from schemas.plan import PlanCreate, PlanUpdate, PlanOut, PlanWithStepsOut, PlanCreateWithSteps, PlanUpdateWithSteps
from crud.steps import get_steps_for_plan

router = APIRouter(prefix="/plans", tags=["Plans"])

@router.post("/", response_model=PlanOut)
async def create_plan_endpoint(plan_in: PlanCreate, hypothesis_id: UUID, db: AsyncSession = Depends(get_db)):
    return await create_plan(db, hypothesis_id, plan_in)

@router.post("/with_steps", response_model=PlanWithStepsOut)
async def create_plan_with_steps_endpoint(
    plan_in: PlanCreateWithSteps,
    hypothesis_id: UUID,
    db: AsyncSession = Depends(get_db)
):

    plan = await create_plan_with_steps(db, hypothesis_id, plan_in)
    steps = await get_steps_for_plan(db, plan.id)
    return PlanWithStepsOut(
        id=plan.id,
        hypothesis_id=plan.hypothesis_id,
        study_goal=plan.study_goal,
        state=plan.state,
        steps=[PlanStepOut.model_validate(s) for s in steps]
    )
  

@router.get("/", response_model=List[PlanOut])
async def list_plans(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
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

@router.get("/by-hypothesis/{hypothesis_id}", response_model=List[PlanWithStepsOut])
async def read_plan_by_hypothesis(
    hypothesis_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    plans = await get_plans_for_hypothesis(db, hypothesis_id, skip=skip, limit=limit)
    results = []
    for plan in plans:
        steps_result = await db.execute(
            select(PlanStep)
            .where(PlanStep.plan_id == plan.id)
            .options(
                selectinload(PlanStep.task_message)
                .selectinload(TaskMessage.context_augmentation)
            )
        )
        steps = steps_result.scalars().all()
        results.append(
            PlanWithStepsOut(
                id=plan.id,
                hypothesis_id=plan.hypothesis_id,
                study_goal=plan.study_goal,
                state=plan.state,
                steps=steps
            )
        )
    return results

@router.patch("/{plan_id}", response_model=PlanOut)
async def edit_plan(plan_id: UUID, plan_in: PlanUpdate, db: AsyncSession = Depends(get_db)):
    return await update_plan(db, plan_id, plan_in)

@router.patch("/{plan_id}/with_steps", response_model=PlanWithStepsOut)
async def update_plan_with_steps_endpoint(
    plan_id: UUID,
    plan_in: PlanUpdateWithSteps,
    db: AsyncSession = Depends(get_db)
):

    plan = await update_plan_with_steps(db, plan_id, plan_in)
    steps = await get_steps_for_plan(db, plan_id)

    return PlanWithStepsOut(
        id=plan.id,
        hypothesis_id=plan.hypothesis_id,
        study_goal=plan.study_goal,
        state=plan.state,
        steps=steps
    )

@router.delete("/{plan_id}")
async def remove_plan(plan_id: UUID, db: AsyncSession = Depends(get_db)):
    return await delete_plan(db, plan_id)
