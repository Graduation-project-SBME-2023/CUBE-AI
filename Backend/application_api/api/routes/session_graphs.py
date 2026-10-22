from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.api.dependencies import owned_session
from application_api.db.session import get_db
from application_api.repositories import graph_changes as changes_repo
from application_api.repositories.session_graphs import graph_view
from application_api.schemas.graph_changes import GraphChangeResult, GraphChangeSubmit
from application_api.schemas.session_graphs import SessionGraphView

router = APIRouter(prefix="/session-graphs", tags=["Session graphs"])


@router.get("/session/{session_id}", response_model=SessionGraphView)
async def read_graph_for_session(
    session_id: UUID = Depends(owned_session), db: AsyncSession = Depends(get_db)
):
    """Version, live nodes with their current runs, the pending change and the
    derived activity."""
    return await graph_view(db, session_id)


@router.post("/session/{session_id}/changes", response_model=GraphChangeResult)
async def submit_change(
    data: GraphChangeSubmit,
    session_id: UUID = Depends(owned_session),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key", max_length=256),
    db: AsyncSession = Depends(get_db),
):
    """Applied at once when no approval reason applies, proposed otherwise
    (pending). 409 `STALE_BASE` or `IDEMPOTENCY_CONFLICT`; 422 `INVALID_CHANGE`."""
    return await changes_repo.submit_change(db, session_id, data, idempotency_key)
