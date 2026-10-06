from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.api.dependencies import owned_node, owned_session
from application_api.db.session import get_db
from application_api.repositories.graph_nodes import get_node, get_nodes_for_session
from application_api.schemas.graph_nodes import GraphNodeOut

# Read-only: nodes are written by applied graph changes alone.
router = APIRouter(prefix="/graph-nodes", tags=["Graph nodes"])


@router.get("/session/{session_id}", response_model=list[GraphNodeOut])
async def list_nodes_for_session(
    session_id: UUID = Depends(owned_session),
    include_removed: bool = False,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    return await get_nodes_for_session(db, session_id, include_removed, skip, limit)


@router.get("/{node_id}", response_model=GraphNodeOut)
async def read_node(node_id: UUID, _: UUID = Depends(owned_node), db: AsyncSession = Depends(get_db)):
    return await get_node(db, node_id)
