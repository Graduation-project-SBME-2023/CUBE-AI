from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.repositories.graph_nodes import (
    add_node_to_graph,
    delete_node,
    get_all_nodes,
    get_node,
    get_nodes_for_session,
    update_node,
)
from application_api.schemas.graph_nodes import GraphNodeCreate, GraphNodeOut, GraphNodeUpdate

router = APIRouter(prefix="/graph-nodes", tags=["Graph nodes"])


@router.post("/session/{session_id}", response_model=GraphNodeOut, status_code=201)
async def create_node(
    session_id: UUID, node_in: GraphNodeCreate, db: AsyncSession = Depends(get_db)
):
    """Append a node to a session's draft graph."""
    return await add_node_to_graph(db, session_id, node_in)


@router.get("/", response_model=list[GraphNodeOut])
async def list_nodes(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await get_all_nodes(db, skip, limit)


@router.get("/session/{session_id}", response_model=list[GraphNodeOut])
async def list_nodes_for_session(
    session_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await get_nodes_for_session(db, session_id, skip=skip, limit=limit)


@router.get("/{node_id}", response_model=GraphNodeOut)
async def read_node(node_id: UUID, db: AsyncSession = Depends(get_db)):
    return await get_node(db, node_id)


@router.patch("/{node_id}", response_model=GraphNodeOut)
async def edit_node(
    node_id: UUID, node_in: GraphNodeUpdate, db: AsyncSession = Depends(get_db)
):
    """Edit a draft node. 409 once the graph is approved."""
    return await update_node(db, node_id, node_in)


@router.delete("/{node_id}")
async def remove_node(node_id: UUID, db: AsyncSession = Depends(get_db)):
    """Delete a draft node and compact the remaining positions."""
    return await delete_node(db, node_id)
