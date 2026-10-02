"""Publication — one short transaction that commits a domain change and its event together, under the session lock."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.core.json_bounds import (
    SESSION_EVENT_PAYLOAD_MAX_BYTES,
    JsonBoundsError,
    check_json,
)
from application_api.db.models.session_events import SessionEvent

logger = logging.getLogger(__name__)


async def lock_session(db: AsyncSession, session_id: UUID | str) -> None:
    """Serialize event allocation in commit order; 404 if the session is absent."""
    locked = (
        await db.execute(
            text(
                "SELECT session_id FROM sessions WHERE session_id = :sid "
                "FOR NO KEY UPDATE"
            ),
            {"sid": str(session_id)},
        )
    ).scalar_one_or_none()
    if locked is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")


async def lock_session_and_graph(db: AsyncSession, session_id: UUID | str) -> None:
    """Session first, then its session graph — the order every writer must use."""
    await lock_session(db, session_id)
    await db.execute(
        text("SELECT session_id FROM session_graphs WHERE session_id = :sid FOR NO KEY UPDATE"),
        {"sid": str(session_id)},
    )


def stage_event(
    db: AsyncSession,
    session_id: UUID | str,
    event_type: str,
    payload: Any,
) -> SessionEvent:
    """Add one event to the *current* transaction."""
    if not event_type or not event_type.strip():
        raise HTTPException(status_code=422, detail="event_type is required")
    try:
        check_json(payload, max_bytes=SESSION_EVENT_PAYLOAD_MAX_BYTES, what="event payload")
    except JsonBoundsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    event = SessionEvent(session_id=session_id, event_type=event_type, payload=payload)
    db.add(event)
    return event


@asynccontextmanager
async def publication(db: AsyncSession, session_id: UUID | str):
    """One publication transaction: lock the session, do the work, commit once."""
    await lock_session(db, session_id)
    staged: list[SessionEvent] = []

    def publish(event_type: str, payload: Any) -> SessionEvent:
        staged.append(stage_event(db, session_id, event_type, payload))
        return staged[-1]

    try:
        yield publish
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("Publication failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    except Exception:
        await db.rollback()
        raise

    for event in staged:
        # Refreshed after commit so the caller can hand the client its cursor.
        await db.refresh(event)
