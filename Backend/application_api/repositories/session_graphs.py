"""Session-graph repository: the graph view the Executor and the UI read.

The graph changes only through graph changes (`graph_changes.py`)."""

from __future__ import annotations

import logging
from uuid import UUID

from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.core.execution_rules import ActiveAttemptFacts, NodeFacts, next_step

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


# The session's control state and its non-terminal attempt, with the live node
# of that attempt's run (NULL once the run is superseded or the node removed).
_NEXT_STEP_FACTS = text(
    """
    SELECT s.control_state, a.state, a.suspension ->> 'kind' AS suspension_kind,
           COALESCE(a.suspension ? 'resume', false) AS answered,
           CASE WHEN r.superseded_at IS NULL AND n.removed_at IS NULL THEN n.node_id END AS node_id
    FROM sessions s
    LEFT JOIN run_attempts a ON a.session_id = s.session_id AND run_attempt_state_is_active(a.state)
    LEFT JOIN node_runs r ON r.run_id = a.run_id
    LEFT JOIN graph_nodes n ON n.node_id = r.node_id
    WHERE s.session_id = :sid
    LIMIT 1
    """
)


def _holds(run: Optional[NodeRun]) -> bool:
    return bool(run is not None and run.downstream_hold and run.hold_released_by_change_id is None)


async def _next_step(db: AsyncSession, session_id: UUID, rows: list[Any]) -> Optional[schemas.NextStepOut]:
    """What the Executor does next, from the live nodes already read and the session's active attempt."""
    facts = (await db.execute(_NEXT_STEP_FACTS, {"sid": str(session_id)})).one()
    active = None
    if facts.state is not None:
        active = ActiveAttemptFacts(
            state=facts.state, suspension_kind=facts.suspension_kind, answered=facts.answered,
            node_id=str(facts.node_id) if facts.node_id is not None else None,
        )
    nodes = [
        NodeFacts(
            node_id=str(node.node_id), approval=node.approval,
            start_released=node.start_released_by_change_id is not None,
            run_status=run.status if run is not None else None, holds=_holds(run),
        )
        for node, run in rows
    ]
    step = next_step(facts.control_state, nodes, active)
    return schemas.NextStepOut(action=step.action, node_id=step.node_id) if step else None


async def graph_view(db: AsyncSession, session_id: UUID) -> schemas.SessionGraphView:
    """Version, live nodes with their current runs and latest attempts, the
    pending change, the derived activity and the Executor's next step."""
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
        next_step=await _next_step(db, session_id, rows),
    )
