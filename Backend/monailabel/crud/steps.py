# app/crud/plan_step.py
from fastapi import HTTPException
from uuid import UUID, uuid4

from models.steps import PlanStep
from schemas.steps import PlanStepCreate, PlanStepUpdate
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.exc import SQLAlchemyError


async def add_step_to_plan(db: AsyncSession, plan_id: UUID, step_in: PlanStepCreate):
    try:
        result = await db.execute(
            select(PlanStep.order).where(PlanStep.plan_id == plan_id).order_by(desc(PlanStep.order))
        )
        last_order = result.scalar()
        next_order = 1 if last_order is None else last_order + 1

        new_step = PlanStep(id=uuid4(), plan_id=plan_id, order=next_order, **step_in.dict())
        db.add(new_step)
        await db.commit()
        await db.refresh(new_step)
        return new_step
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def get_step(db: AsyncSession, step_id: UUID):
    try:
        result = await db.execute(select(PlanStep).where(PlanStep.id == step_id))
        step = result.scalar_one_or_none()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")
        return step
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def get_steps_for_plan(db: AsyncSession, plan_id: UUID):
    try:
        result = await db.execute(
            select(PlanStep).where(PlanStep.plan_id == plan_id).order_by(PlanStep.order)
        )
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def get_all_steps(db: AsyncSession, skip: int = 0, limit: int = 100):
    try:
        result = await db.execute(select(PlanStep).offset(skip).limit(limit))
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def update_step(db: AsyncSession, step_id: UUID, step_in: PlanStepUpdate):
    step = await get_step(db, step_id)
    for field, value in step_in.dict(exclude_unset=True).items():
        setattr(step, field, value)
    try:
        await db.commit()
        await db.refresh(step)
        return step
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def delete_step(db: AsyncSession, step_id: UUID):
    step = await get_step(db, step_id)
    try:
        await db.delete(step)
        await db.commit()
        return {"detail": "Step deleted"}
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
