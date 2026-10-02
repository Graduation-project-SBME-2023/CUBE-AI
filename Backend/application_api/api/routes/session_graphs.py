from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.repositories.session_graphs import (
    approve_session_graph,
    delete_session_graph,
    get_all_session_graphs,
    get_session_graph,
    replace_draft_nodes,
)
from application_api.schemas.session_graphs import (
    SessionGraphDraftUpdate,
    SessionGraphOut,
    SessionGraphWithNodesOut,
)

router = APIRouter(prefix="/session-graphs", tags=["Session graphs"])


@router.get("/", response_model=list[SessionGraphWithNodesOut])
async def list_session_graphs(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await get_all_session_graphs(db, skip, limit)


@router.get("/session/{session_id}", response_model=SessionGraphWithNodesOut)
async def read_graph_for_session(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """A graph is addressed by its session: `session_graphs.session_id` is the primary key."""
    return await get_session_graph(db, session_id)


@router.put("/session/{session_id}/nodes", response_model=SessionGraphWithNodesOut)
async def replace_graph_nodes(
    session_id: UUID, data: SessionGraphDraftUpdate, db: AsyncSession = Depends(get_db)
):
    """Replace the draft's ordered node list."""
    return await replace_draft_nodes(db, session_id, data)


@router.post("/session/{session_id}/approve", response_model=SessionGraphOut)
async def approve_session_graph_endpoint(
    session_id: UUID, db: AsyncSession = Depends(get_db)
):
    """Approve the graph. One-way, atomic, and 409 on a second call."""
    return await approve_session_graph(db, session_id)


@router.delete("/session/{session_id}")
async def remove_session_graph(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """Always 409: a graph is not deletable independently of its session."""
    return await delete_session_graph(db, session_id)
