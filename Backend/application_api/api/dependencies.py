"""Request-scoped dependencies shared by the routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.core.config import settings
from application_api.db.models.graph_changes import GraphChange
from application_api.db.models.graph_nodes import GraphNode
from application_api.db.models.node_runs import NodeRun
from application_api.db.session import get_db
from application_api.repositories.users import require_owner, resolve_user_id


async def current_user(db: AsyncSession = Depends(get_db)) -> UUID:
    """The caller's user id: the configured default user until authentication
    maps a verified login to a username."""
    return await resolve_user_id(db, settings.DEFAULT_USERNAME)


async def owned_session(
    session_id: UUID,
    user_id: UUID = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    """The `{session_id}` of a session-scoped route, owned by the caller."""
    await require_owner(db, session_id, user_id)
    return session_id


async def _owned(db: AsyncSession, user_id: UUID, query, what: str, key: UUID) -> UUID:
    session_id = (await db.execute(query)).scalar()
    if session_id is None:
        raise HTTPException(status_code=404, detail=f"{what} {key} not found")
    await require_owner(db, session_id, user_id)
    return session_id


async def owned_run(
    run_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    """The session of `{run_id}`, owned by the caller."""
    return await _owned(db, user_id, select(NodeRun.session_id).where(NodeRun.run_id == run_id), "Run", run_id)


async def owned_change(
    change_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    query = select(GraphChange.session_id).where(GraphChange.change_id == change_id)
    return await _owned(db, user_id, query, "Graph change", change_id)


async def owned_node(
    node_id: UUID, user_id: UUID = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> UUID:
    query = select(GraphNode.session_id).where(GraphNode.node_id == node_id)
    return await _owned(db, user_id, query, "Node", node_id)
