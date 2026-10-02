"""Session-graph repository"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from application_api.core.agent_types import unknown_agent_types
from application_api.db.models.graph_nodes import GraphNode
from application_api.db.models.session_graphs import SessionGraph
from application_api.db.models.sessions import Session as SessionModel
from application_api.repositories.node_runs import create_pending_runs, list_runs
from application_api.schemas.session_graphs import SessionGraphDraftUpdate
from application_api.services.publication import lock_session_and_graph

logger = logging.getLogger(__name__)


async def create_session_with_graph(db: AsyncSession, session: SessionModel) -> SessionGraph:
    """Add the empty graph for a session inside the caller's transaction."""
    graph = SessionGraph(session_id=session.session_id, approved_at=None)
    db.add(graph)
    return graph


async def get_session_graph(db: AsyncSession, session_id: UUID) -> SessionGraph:
    graph = (
        await db.execute(
            select(SessionGraph)
            .options(selectinload(SessionGraph.nodes))
            .where(SessionGraph.session_id == session_id)
        )
    ).unique().scalar_one_or_none()
    if graph is None:
        raise HTTPException(
            status_code=404, detail=f"No session graph for session {session_id}"
        )
    return graph


async def get_all_session_graphs(
    db: AsyncSession, skip: int = 0, limit: int = 100
) -> list[SessionGraph]:
    result = await db.execute(
        select(SessionGraph)
        .options(selectinload(SessionGraph.nodes))
        .order_by(SessionGraph.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.unique().scalars().all())


async def _require_draft(db: AsyncSession, session_id: UUID) -> SessionGraph:
    await lock_session_and_graph(db, session_id)
    graph = await get_session_graph(db, session_id)
    if graph.approved_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"The graph for session {session_id} was approved at "
                f"{graph.approved_at.isoformat()}. An approved graph and its nodes "
                "are immutable; a changed graph requires a new session."
            ),
        )
    return graph


async def replace_draft_nodes(
    db: AsyncSession, session_id: UUID, data: SessionGraphDraftUpdate
) -> SessionGraph:
    """Replace the draft's ordered node list."""
    graph = await _require_draft(db, session_id)

    existing = (
        await db.execute(select(GraphNode).where(GraphNode.session_id == session_id))
    ).scalars().all()
    for node in existing:
        await db.delete(node)
    await db.flush()

    for position, node_in in enumerate(data.nodes, start=1):
        db.add(
            GraphNode(
                session_id=session_id,
                position=position,
                target_name=node_in.target_name,
                title=node_in.title,
                params=node_in.params,
            )
        )

    graph.updated_at = datetime.now(timezone.utc)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Graph draft rejected: {e}")
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    # `AsyncSessionLocal` is `expire_on_commit=False`, so `graph.nodes` — loaded
    # empty (or stale) by `_require_draft`'s earlier `get_session_graph` call —
    # is not invalidated by the commit above. The individual GraphNode rows were
    # deleted/added directly rather than through the `graph.nodes` collection,
    # so without expiring it explicitly, the `selectinload` in
    # `get_session_graph` below reuses the already-loaded (now stale) collection
    # instead of re-querying it, and the caller gets back the nodes it just
    # replaced.
    db.expire(graph, ["nodes"])
    return await get_session_graph(db, session_id)


async def approve_session_graph(db: AsyncSession, session_id: UUID) -> SessionGraph:
    """ one way, atomic approval."""
    # _require_draft takes the session lock and then the graph lock — the order
    # asks for, held through commit.
    graph = await _require_draft(db, session_id)
    if not graph.nodes:
        raise HTTPException(
            status_code=409,
            detail="An empty graph cannot be approved.",
        )

    positions = [node.position for node in graph.nodes]
    if sorted(positions) != list(range(1, len(positions) + 1)):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Graph node positions are not a contiguous 1..n sequence: {sorted(positions)}."
            ),
        )

    unknown = unknown_agent_types([node.target_name for node in graph.nodes])
    if unknown:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Unknown target_name(s): {', '.join(unknown)}. A node must name an "
                "agent that exists in AGENT_FACTORIES; unknown keys are rejected "
                "before admission, never silently skipped."
            ),
        )

    existing_runs = await list_runs(db, session_id)
    if existing_runs:
        # Only an approved graph can own runs (the deferred trigger enforces it),
        # so this state is unreachable through the API. Refuse rather than
        # create a second set.
        raise HTTPException(
            status_code=409,
            detail=(
                f"Session {session_id} already has {len(existing_runs)} run(s) "
                "for a draft graph; reconciliation is required before approval."
            ),
        )

    graph.approved_at = datetime.now(timezone.utc)
    graph.updated_at = graph.approved_at
    await create_pending_runs(db, list(graph.nodes))
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Approval rejected: {e}")
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    return await get_session_graph(db, session_id)


async def delete_session_graph(db: AsyncSession, session_id: UUID):
    """Refused the session exists — the graph is not independently deletable; archive the session instead."""
    raise HTTPException(
        status_code=409,
        detail=(
            f"The graph for session {session_id} cannot be deleted independently "
            "of its session. Archive the session instead."
        ),
    )
