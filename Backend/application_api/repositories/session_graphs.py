"""Session-graph repository: the graph view the Executor and the UI read.

The graph changes only through graph changes (`graph_changes.py`)."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.graph_nodes import GraphNode
from application_api.db.models.node_runs import NodeRun
from application_api.db.models.run_attempts import RunAttempt
from application_api.db.models.session_graphs import SessionGraph
from application_api.repositories import graph_changes as changes_repo
from application_api.repositories import sessions as sessions_repo
from application_api.schemas import session_graphs as schemas

logger = logging.getLogger(__name__)


async def get_session_graph(db: AsyncSession, session_id: UUID) -> SessionGraph:
    graph = (
        await db.execute(
            select(SessionGraph).where(SessionGraph.session_id == session_id)
        )
    ).scalar_one_or_none()
    if graph is None:
        raise HTTPException(status_code=404, detail=f"No session graph for session {session_id}")
    return graph


async def graph_view(db: AsyncSession, session_id: UUID) -> schemas.SessionGraphView:
    """Version, live nodes with their current runs and latest attempts, the
    pending change and the derived activity."""
    graph = await get_session_graph(db, session_id)
    rows = (
        await db.execute(
            select(GraphNode, NodeRun)
            .outerjoin(NodeRun, (NodeRun.node_id == GraphNode.node_id) & NodeRun.superseded_at.is_(None))
            .where(GraphNode.session_id == session_id, GraphNode.removed_at.is_(None))
            .order_by(GraphNode.position)
        )
    ).all()
    run_ids = [run.run_id for _, run in rows if run is not None]
    latest: dict[UUID, RunAttempt] = {}
    if run_ids:
        attempts = (
            await db.execute(
                select(RunAttempt)
                .where(RunAttempt.run_id.in_(run_ids))
                .distinct(RunAttempt.run_id)
                .order_by(RunAttempt.run_id, RunAttempt.attempt_number.desc())
            )
        ).scalars()
        latest = {attempt.run_id: attempt for attempt in attempts}
    return schemas.SessionGraphView(
        session_id=graph.session_id,
        version=graph.version,
        updated_at=graph.updated_at,
        nodes=[
            schemas.LiveNodeOut(
                node=node, run=run, attempt=latest.get(run.run_id) if run is not None else None
            )
            for node, run in rows
        ],
        pending_change=await changes_repo.pending_change(db, session_id),
        activity=await sessions_repo.session_activity(db, session_id),
    )
