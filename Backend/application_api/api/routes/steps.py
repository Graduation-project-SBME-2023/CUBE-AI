from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.repositories.steps import (
    add_step_to_plan,
    delete_step,
    get_all_steps,
    get_step,
    get_steps_for_session,
    update_step,
)
from application_api.schemas.steps import PlanStepCreate, PlanStepOut, PlanStepUpdate

router = APIRouter(prefix="/steps", tags=["Plan Steps"])


@router.post("/session/{session_id}", response_model=PlanStepOut, status_code=201)
async def create_step(
    session_id: UUID, step_in: PlanStepCreate, db: AsyncSession = Depends(get_db)
):
    """Append a step to a session's draft plan."""
    return await add_step_to_plan(db, session_id, step_in)


@router.get("/", response_model=list[PlanStepOut])
async def list_steps(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await get_all_steps(db, skip, limit)


@router.get("/session/{session_id}", response_model=list[PlanStepOut])
async def list_steps_for_session(
    session_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await get_steps_for_session(db, session_id, skip=skip, limit=limit)


@router.get("/{step_id}", response_model=PlanStepOut)
async def read_step(step_id: UUID, db: AsyncSession = Depends(get_db)):
    return await get_step(db, step_id)


@router.patch("/{step_id}", response_model=PlanStepOut)
async def edit_step(
    step_id: UUID, step_in: PlanStepUpdate, db: AsyncSession = Depends(get_db)
):
    """Edit a draft step. 409 once the plan is approved."""
    return await update_step(db, step_id, step_in)


@router.delete("/{step_id}")
async def remove_step(step_id: UUID, db: AsyncSession = Depends(get_db)):
    """Delete a draft step and compact the remaining step_order values."""
    return await delete_step(db, step_id)
