"""Plan-step repository"""

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.plan import Plan
from application_api.db.models.steps import PlanStep
from application_api.schemas.steps import PlanStepCreate, PlanStepUpdate
from application_api.services.publication import lock_session_and_plan

logger = logging.getLogger(__name__)


async def _require_draft_plan(db: AsyncSession, session_id: UUID) -> Plan:
    """Every step becomes immutable when its plan is approved."""
    await lock_session_and_plan(db, session_id)
    plan = (
        await db.execute(select(Plan).where(Plan.session_id == session_id))
    ).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail=f"No plan for session {session_id}")
    if plan.approved_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"The plan for session {session_id} was approved at "
                f"{plan.approved_at.isoformat()}; its steps are immutable. A "
                "changed plan requires a new session."
            ),
        )
    return plan


async def add_step_to_plan(
    db: AsyncSession, session_id: UUID, step_in: PlanStepCreate
) -> PlanStep:
    plan = await _require_draft_plan(db, session_id)
    try:
        last_order = (
            await db.execute(
                select(PlanStep.step_order)
                .where(PlanStep.session_id == session_id)
                .order_by(desc(PlanStep.step_order))
            )
        ).scalar()
        next_order = 1 if last_order is None else last_order + 1

        new_step = PlanStep(
            step_id=uuid4(),
            session_id=session_id,
            step_order=next_order,
            agent_type=step_in.agent_type,
            title=step_in.title,
            instructions=step_in.instructions,
        )
        db.add(new_step)
        await db.flush()

        plan.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return await get_step(db, new_step.step_id)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Step rejected: {e}")
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def get_step(db: AsyncSession, step_id: UUID) -> PlanStep:
    try:
        step = (
            await db.execute(select(PlanStep).where(PlanStep.step_id == step_id))
        ).scalar_one_or_none()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")
        return step
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def get_steps_for_session(
    db: AsyncSession, session_id: UUID, skip: int = 0, limit: int = 100
) -> list[PlanStep]:
    try:
        result = await db.execute(
            select(PlanStep)
            .where(PlanStep.session_id == session_id)
            .order_by(PlanStep.step_order)
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def get_all_steps(db: AsyncSession, skip: int = 0, limit: int = 100) -> list[PlanStep]:
    try:
        result = await db.execute(
            select(PlanStep)
            .order_by(PlanStep.session_id, PlanStep.step_order)
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def update_step(db: AsyncSession, step_id: UUID, step_in: PlanStepUpdate) -> PlanStep:
    step = await get_step(db, step_id)
    plan = await _require_draft_plan(db, step.session_id)

    for field, value in step_in.model_dump(exclude_unset=True).items():
        setattr(step, field, value)

    plan.updated_at = datetime.now(timezone.utc)
    try:
        await db.commit()
        return await get_step(db, step_id)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Step update rejected: {e}")
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def delete_step(db: AsyncSession, step_id: UUID) -> dict:
    step = await get_step(db, step_id)
    plan = await _require_draft_plan(db, step.session_id)
    session_id = step.session_id
    try:
        await db.delete(step)
        await db.flush()
        # Keep step_order a contiguous 1..n sequence; approval rejects a plan
        # whose ordering has gaps.
        remaining = (
            await db.execute(
                select(PlanStep)
                .where(PlanStep.session_id == session_id)
                .order_by(PlanStep.step_order)
            )
        ).scalars().all()
        for position, remaining_step in enumerate(remaining, start=1):
            remaining_step.step_order = position
        plan.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return {"detail": "Step deleted"}
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
