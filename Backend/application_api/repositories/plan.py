"""Plan repository"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from application_api.core.agent_types import unknown_agent_types
from application_api.db.models.plan import Plan
from application_api.db.models.sessions import Session as SessionModel
from application_api.db.models.steps import PlanStep
from application_api.repositories.agent_runs import create_pending_runs, list_runs
from application_api.schemas.plan import PlanDraftUpdate
from application_api.services.publication import lock_session_and_plan

logger = logging.getLogger(__name__)


async def create_session_with_plan(db: AsyncSession, session: SessionModel) -> Plan:
    """Add the empty plan for a session inside the caller's transaction."""
    plan = Plan(session_id=session.session_id, approved_at=None)
    db.add(plan)
    return plan


async def get_plan(db: AsyncSession, session_id: UUID) -> Plan:
    plan = (
        await db.execute(
            select(Plan)
            .options(selectinload(Plan.steps))
            .where(Plan.session_id == session_id)
        )
    ).unique().scalar_one_or_none()
    if plan is None:
        raise HTTPException(
            status_code=404, detail=f"No plan for session {session_id}"
        )
    return plan


async def get_all_plans(db: AsyncSession, skip: int = 0, limit: int = 100) -> list[Plan]:
    result = await db.execute(
        select(Plan)
        .options(selectinload(Plan.steps))
        .order_by(Plan.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.unique().scalars().all())


async def _require_draft(db: AsyncSession, session_id: UUID) -> Plan:
    await lock_session_and_plan(db, session_id)
    plan = await get_plan(db, session_id)
    if plan.approved_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"The plan for session {session_id} was approved at "
                f"{plan.approved_at.isoformat()}. An approved plan and its steps "
                "are immutable; a changed plan requires a new session."
            ),
        )
    return plan


async def replace_draft_steps(
    db: AsyncSession, session_id: UUID, data: PlanDraftUpdate
) -> Plan:
    """Replace the draft's ordered step list."""
    plan = await _require_draft(db, session_id)

    existing = (
        await db.execute(select(PlanStep).where(PlanStep.session_id == session_id))
    ).scalars().all()
    for step in existing:
        await db.delete(step)
    await db.flush()

    for position, step_in in enumerate(data.steps, start=1):
        db.add(
            PlanStep(
                session_id=session_id,
                step_order=position,
                agent_type=step_in.agent_type,
                title=step_in.title,
                instructions=step_in.instructions,
            )
        )

    plan.updated_at = datetime.now(timezone.utc)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Plan draft rejected: {e}")
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    # `AsyncSessionLocal` is `expire_on_commit=False`, so `plan.steps` — loaded
    # empty (or stale) by `_require_draft`'s earlier `get_plan` call — is not
    # invalidated by the commit above. The individual PlanStep rows were
    # deleted/added directly rather than through the `plan.steps` collection,
    # so without expiring it explicitly, the `selectinload` in `get_plan`
    # below reuses the already-loaded (now stale) collection instead of
    # re-querying it, and the caller gets back the steps it just replaced.
    db.expire(plan, ["steps"])
    return await get_plan(db, session_id)


async def approve_plan(db: AsyncSession, session_id: UUID) -> Plan:
    """ one way, atomic approval."""
    # _require_draft takes the session lock and then the plan lock — the order
    # asks for, held through commit.
    plan = await _require_draft(db, session_id)
    if not plan.steps:
        raise HTTPException(
            status_code=409,
            detail="An empty plan cannot be approved.",
        )

    orders = [step.step_order for step in plan.steps]
    if sorted(orders) != list(range(1, len(orders) + 1)):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Plan step order is not a contiguous 1..n sequence: {sorted(orders)}."
            ),
        )

    unknown = unknown_agent_types([step.agent_type for step in plan.steps])
    if unknown:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Unknown agent_type(s): {', '.join(unknown)}. A step must name an "
                "agent that exists in AGENT_FACTORIES; unknown keys are rejected "
                "before admission, never silently skipped."
            ),
        )

    existing_runs = await list_runs(db, session_id)
    if existing_runs:
        # Only an approved plan can own runs (the deferred trigger enforces it),
        # so this state is unreachable through the API. Refuse rather than
        # create a second set.
        raise HTTPException(
            status_code=409,
            detail=(
                f"Session {session_id} already has {len(existing_runs)} agent run(s) "
                "for a draft plan; reconciliation is required before approval."
            ),
        )

    plan.approved_at = datetime.now(timezone.utc)
    plan.updated_at = plan.approved_at
    await create_pending_runs(db, list(plan.steps))
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Approval rejected: {e}")
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    return await get_plan(db, session_id)


async def delete_plan(db: AsyncSession, session_id: UUID):
    """Refused the session exists — the plan is not independently deletable; archive the session instead."""
    raise HTTPException(
        status_code=409,
        detail=(
            f"The plan for session {session_id} cannot be deleted independently "
            "of its session. Archive the session instead."
        ),
    )
