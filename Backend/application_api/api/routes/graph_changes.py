from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.api.dependencies import owned_change, owned_session
from application_api.db.session import get_db
from application_api.repositories import graph_changes as changes_repo
from application_api.schemas.graph_changes import (
    GraphChangeDecision,
    GraphChangeHistory,
    GraphChangeOut,
    GraphChangeResult,
)

router = APIRouter(prefix="/graph-changes", tags=["Graph changes"])


@router.post("/{change_id}/decision", response_model=GraphChangeResult)
async def decide_change(
    change_id: UUID,
    data: GraphChangeDecision,
    _: UUID = Depends(owned_change),
    db: AsyncSession = Depends(get_db),
):
    """A user's decision, quoting the exact content digest. Approve
    applies it; a stale or expired change is marked so and refused (409).
    Called by the user's UI only, never by an agent."""
    return await changes_repo.decide_change(db, change_id, data)


@router.get("/session/{session_id}", response_model=GraphChangeHistory)
async def list_session_changes(
    session_id: UUID = Depends(owned_session),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """The history, newest first, and the pending change."""
    changes = await changes_repo.list_changes(db, session_id, limit)
    return {"pending": next((c for c in changes if c.status == "pending"), None), "changes": changes}


@router.get("/{change_id}", response_model=GraphChangeOut)
async def read_change(change_id: UUID, _: UUID = Depends(owned_change), db: AsyncSession = Depends(get_db)):
    return await changes_repo.get_change(db, change_id)
