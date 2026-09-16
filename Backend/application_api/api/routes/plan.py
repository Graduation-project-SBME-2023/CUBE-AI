from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.repositories.plan import (
    approve_plan,
    delete_plan,
    get_all_plans,
    get_plan,
    replace_draft_steps,
)
from application_api.schemas.plan import PlanDraftUpdate, PlanOut, PlanWithStepsOut

router = APIRouter(prefix="/plans", tags=["Plans"])


@router.get("/", response_model=list[PlanWithStepsOut])
async def list_plans(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await get_all_plans(db, skip, limit)


@router.get("/session/{session_id}", response_model=PlanWithStepsOut)
async def read_plan_for_session(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """A plan is addressed by its session: `plans.session_id` is the primary key."""
    return await get_plan(db, session_id)


@router.put("/session/{session_id}/steps", response_model=PlanWithStepsOut)
async def replace_plan_steps(
    session_id: UUID, data: PlanDraftUpdate, db: AsyncSession = Depends(get_db)
):
    """Replace the draft's ordered step list."""
    return await replace_draft_steps(db, session_id, data)


@router.post("/session/{session_id}/approve", response_model=PlanOut)
async def approve_plan_endpoint(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """Approve the plan. One-way, atomic, and 409 on a second call."""
    return await approve_plan(db, session_id)


@router.delete("/session/{session_id}")
async def remove_plan(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """Always 409: a plan is not deletable independently of its session."""
    return await delete_plan(db, session_id)
