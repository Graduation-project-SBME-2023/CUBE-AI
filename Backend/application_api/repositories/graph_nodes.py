"""Graph-node repository: reads only. A node's definition changes only through
an applied graph change (`graph_changes.py`)."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.graph_nodes import GraphNode


async def get_node(db: AsyncSession, node_id: UUID) -> GraphNode:
    node = (await db.execute(select(GraphNode).where(GraphNode.node_id == node_id))).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    return node


async def get_nodes_for_session(
    db: AsyncSession, session_id: UUID, include_removed: bool = False, skip: int = 0, limit: int = 100
) -> list[GraphNode]:
    """Live nodes in execution order; with removed ones, every node by handle."""
    query = select(GraphNode).where(GraphNode.session_id == session_id)
    if include_removed:
        query = query.order_by(GraphNode.node_seq)
    else:
        query = query.where(GraphNode.removed_at.is_(None)).order_by(GraphNode.position)
    return list((await db.execute(query.offset(skip).limit(limit))).scalars().all())
