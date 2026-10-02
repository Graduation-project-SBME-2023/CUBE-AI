"""Graph-node repository"""

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.graph_nodes import GraphNode
from application_api.db.models.session_graphs import SessionGraph
from application_api.schemas.graph_nodes import GraphNodeCreate, GraphNodeUpdate
from application_api.services.publication import lock_session_and_graph

logger = logging.getLogger(__name__)


async def _require_draft_graph(db: AsyncSession, session_id: UUID) -> SessionGraph:
    """Every node becomes immutable when its graph is approved."""
    await lock_session_and_graph(db, session_id)
    graph = (
        await db.execute(select(SessionGraph).where(SessionGraph.session_id == session_id))
    ).scalar_one_or_none()
    if graph is None:
        raise HTTPException(
            status_code=404, detail=f"No session graph for session {session_id}"
        )
    if graph.approved_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"The graph for session {session_id} was approved at "
                f"{graph.approved_at.isoformat()}; its nodes are immutable. A "
                "changed graph requires a new session."
            ),
        )
    return graph


async def add_node_to_graph(
    db: AsyncSession, session_id: UUID, node_in: GraphNodeCreate
) -> GraphNode:
    graph = await _require_draft_graph(db, session_id)
    try:
        last_position = (
            await db.execute(
                select(GraphNode.position)
                .where(GraphNode.session_id == session_id)
                .order_by(desc(GraphNode.position))
            )
        ).scalar()
        next_position = 1 if last_position is None else last_position + 1

        new_node = GraphNode(
            node_id=uuid4(),
            session_id=session_id,
            position=next_position,
            target_name=node_in.target_name,
            title=node_in.title,
            params=node_in.params,
        )
        db.add(new_node)
        await db.flush()

        graph.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return await get_node(db, new_node.node_id)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Node rejected: {e}")
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def get_node(db: AsyncSession, node_id: UUID) -> GraphNode:
    try:
        node = (
            await db.execute(select(GraphNode).where(GraphNode.node_id == node_id))
        ).scalar_one_or_none()
        if not node:
            raise HTTPException(status_code=404, detail="Node not found")
        return node
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def get_nodes_for_session(
    db: AsyncSession, session_id: UUID, skip: int = 0, limit: int = 100
) -> list[GraphNode]:
    try:
        result = await db.execute(
            select(GraphNode)
            .where(GraphNode.session_id == session_id)
            .order_by(GraphNode.position)
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def get_all_nodes(db: AsyncSession, skip: int = 0, limit: int = 100) -> list[GraphNode]:
    try:
        result = await db.execute(
            select(GraphNode)
            .order_by(GraphNode.session_id, GraphNode.position)
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def update_node(db: AsyncSession, node_id: UUID, node_in: GraphNodeUpdate) -> GraphNode:
    node = await get_node(db, node_id)
    graph = await _require_draft_graph(db, node.session_id)

    for field, value in node_in.model_dump(exclude_unset=True).items():
        setattr(node, field, value)

    graph.updated_at = datetime.now(timezone.utc)
    try:
        await db.commit()
        return await get_node(db, node_id)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Node update rejected: {e}")
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def delete_node(db: AsyncSession, node_id: UUID) -> dict:
    node = await get_node(db, node_id)
    graph = await _require_draft_graph(db, node.session_id)
    session_id = node.session_id
    try:
        await db.delete(node)
        await db.flush()
        # Keep position a contiguous 1..n sequence; approval rejects a graph
        # whose ordering has gaps.
        remaining = (
            await db.execute(
                select(GraphNode)
                .where(GraphNode.session_id == session_id)
                .order_by(GraphNode.position)
            )
        ).scalars().all()
        for position, remaining_node in enumerate(remaining, start=1):
            remaining_node.position = position
        graph.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return {"detail": "Node deleted"}
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
