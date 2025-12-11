# app/crud/plan.py
from fastapi import HTTPException
from sqlalchemy.orm import Session
from uuid import UUID, uuid4

from models.plan import Plan
from schemas.plan import PlanCreate, PlanUpdate
# app/crud/async_plan.py
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from models.hypothesis import Hypothesis

async def create_plan(db: AsyncSession, hypothesis_id: UUID, plan_in: PlanCreate):
    # fetch hypothesis to read title
    result = await db.execute(
        select(Hypothesis).where(Hypothesis.id == hypothesis_id)
    )
    hypothesis = result.scalar_one_or_none()

    if not hypothesis:
        raise HTTPException(status_code=404, detail="Hypothesis not found")

    # auto-set study_goal = hypothesis.title
    study_goal_value = hypothesis.title

    new_plan = Plan(
        id=uuid4(),
        hypothesis_id=hypothesis_id,
        state=plan_in.state,
        study_goal=study_goal_value,   # auto-set here
    )

    try:
        db.add(new_plan)
        await db.commit()
        await db.refresh(new_plan)
        return new_plan
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def get_plan(db: AsyncSession, plan_id: UUID):
    try:
        result = await db.execute(select(Plan).where(Plan.id == plan_id))
        plan = result.scalar_one_or_none()
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")
        return plan
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def get_all_plans(db: AsyncSession, skip: int = 0, limit: int = 100):
    try:
        result = await db.execute(select(Plan).offset(skip).limit(limit))
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    

async def get_plans_for_hypothesis(db: AsyncSession, hypothesis_id: UUID):
    try:
        result = await db.execute(select(Plan).where(Plan.hypothesis_id == hypothesis_id))
        plans = result.scalars().all()
        return plans
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def update_plan(db: AsyncSession, plan_id: UUID, plan_in: PlanUpdate):
    plan = await get_plan(db, plan_id)
    for field, value in plan_in.dict(exclude_unset=True).items():
        setattr(plan, field, value)
    try:
        await db.commit()
        await db.refresh(plan)
        return plan
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def delete_plan(db: AsyncSession, plan_id: UUID):
    plan = await get_plan(db, plan_id)
    try:
        await db.delete(plan)
        await db.commit()
        return {"detail": "Plan deleted"}
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
