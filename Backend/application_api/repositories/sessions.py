"""Session repository: creation, the settings switch, the inbox cursor and the
derived activity."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.core.execution_rules import (
    ActivityFacts,
    LatestAttempt,
    auto_retry_left,
    derive_activity,
)
from application_api.db.models.chat_messages import ChatMessage
from application_api.db.models.chats import Chat
from application_api.db.models.session_graphs import SessionGraph
from application_api.db.models.sessions import Session as SessionModel
from application_api.schemas import sessions as schemas
from application_api.services.publication import conflict, database_refusal, lock_session, publication

logger = logging.getLogger(__name__)

Publish = Callable[[str, Any], Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------------
# Create: session, graph, chat and the opening request, in one transaction
# ----------------------------------------------------------------------
async def create_session(
    db: AsyncSession, data: schemas.SessionCreate, user_id: UUID
) -> SessionModel:
    now = _now()
    session = SessionModel(
        user_id=user_id,
        title=data.title,
        prompt=data.prompt,
        prompt_kind=data.prompt_kind,
        control_state="active",
        execution_mode=data.execution_mode,
        privacy_mode=data.privacy_mode,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise database_refusal(exc) from exc
    # A deferred trigger rejects a committed session with no graph, so the
    # graph, the one chat and the opening message all commit with it.
    chat = Chat(session_id=session.session_id, title=data.title, created_at=now, last_activity_at=now)
    db.add_all([SessionGraph(session_id=session.session_id, version=0, updated_at=now), chat])
    async with publication(db, session.session_id) as publish:
        await db.flush()
        # The Main reads the opening request from the transcript like every
        # other message.
        message = ChatMessage(chat_id=chat.chat_id, role="user", text=data.prompt, created_at=now)
        db.add(message)
        await db.flush()
        publish("user_input", {"chat_id": str(chat.chat_id), "message_id": str(message.message_id)})
    return session


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------
async def get_session(db: AsyncSession, session_id: UUID, user_id: UUID | None = None) -> SessionModel:
    query = (
        select(SessionModel)
        .where(SessionModel.session_id == session_id)
    )
    if user_id is not None:
        query = query.where(SessionModel.user_id == user_id)
    try:
        session = (await db.execute(query)).unique().scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return session


async def get_sessions(
    db: AsyncSession,
    user_id: UUID,
    control_state: str | None = None,
    include_archived: bool = False,
    skip: int = 0,
    limit: int = 50,
) -> list[SessionModel]:
    query = (
        select(SessionModel)
        .where(SessionModel.user_id == user_id)
    )
    if control_state:
        query = query.where(SessionModel.control_state == control_state)
    elif not include_archived:
        query = query.where(SessionModel.control_state != "archived")
    query = query.order_by(SessionModel.updated_at.desc().nullslast()).offset(skip).limit(limit)
    try:
        return list((await db.execute(query)).unique().scalars().all())
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc


# ----------------------------------------------------------------------
# Plain updates
# ----------------------------------------------------------------------
async def update_session(
    db: AsyncSession, session_id: UUID, data: schemas.SessionUpdate, user_id: UUID
) -> SessionModel:
    session = await get_session(db, session_id, user_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(session, field, value)
    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    await db.refresh(session)
    return session


# Control intent only: pause and resume are idempotent, and archiving is
# normal removal. Each entry is the set of states a transition may start from;
# anything else is a 409 rather than a silent overwrite. An archived session
# is final.
_ALLOWED_TRANSITIONS = {
    "active": {"active", "paused"},
    "paused": {"active", "paused"},
    "archived": set(),  # any control state may be archived
}


async def set_session_control_state(
    db: AsyncSession, session_id: UUID, new_state: str, user_id: UUID
) -> SessionModel:
    allowed = _ALLOWED_TRANSITIONS.get(new_state)
    if allowed is None:
        raise HTTPException(status_code=400, detail=f"Unknown control state {new_state!r}")
    session = await get_session(db, session_id, user_id)
    async with publication(db, session_id) as publish:
        await db.refresh(session, attribute_names=["control_state"])
        if allowed and session.control_state not in allowed:
            raise HTTPException(
                status_code=409,
                detail=f"Session {session_id} is {session.control_state!r}; it cannot move to {new_state!r}.",
            )
        if session.control_state != new_state:
            session.control_state = new_state
            await db.flush()
            await publish_session_state(db, session_id, publish)
    await db.refresh(session)
    return session


async def archive_session(db: AsyncSession, session_id: UUID, user_id: UUID) -> SessionModel:
    """Normal removal. This is what a delete request means."""
    return await set_session_control_state(db, session_id, "archived", user_id)


async def delete_session(db: AsyncSession, session_id: UUID, user_id: UUID) -> dict[str, str]:
    """Hard delete, refused while provenance still references the session. The
    FKs are RESTRICT, so the database refuses too; this turns that into a 409."""
    session = await get_session(db, session_id, user_id)
    try:
        await db.delete(session)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"Session {session_id} still has messages, events or a graph referencing it. "
                "Archive it instead of deleting it."
            ),
        ) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete session: {exc}") from exc
    return {"detail": f"Session {session_id} deleted successfully"}


# ----------------------------------------------------------------------
# Settings -- a switch to phi_local cancels `standard` attempts in the same transaction
# ----------------------------------------------------------------------
async def update_settings(
    db: AsyncSession, session_id: UUID, data: schemas.SessionSettingsUpdate, user_id: UUID
) -> SessionModel:
    # Imported here: run_attempts imports this module for the activity event.
    from application_api.repositories import run_attempts as attempts_repo

    session = await get_session(db, session_id, user_id)
    async with publication(db, session_id) as publish:
        await db.refresh(session, attribute_names=["control_state", "execution_mode", "privacy_mode"])
        if session.control_state == "archived":
            raise conflict("ARCHIVED", f"Session {session_id} is archived")
        changes = data.model_dump(exclude_unset=True, exclude_none=True)
        switching_to_local = (
            changes.get("privacy_mode") == "phi_local" and session.privacy_mode != "phi_local"
        )
        for field, value in changes.items():
            setattr(session, field, value)
        await db.flush()
        cancelled = []
        if switching_to_local:
            # A run that started under `standard` must not keep calling
            # external models.
            cancelled = await attempts_repo.request_cancel_where_policy(
                db, session_id, publish, privacy_mode="standard", reason="privacy"
            )
        publish("session_settings", {**changes, "cancel_requested_attempt_ids": cancelled})
    await db.refresh(session)
    return session


# ----------------------------------------------------------------------
# The inbox cursor
# ----------------------------------------------------------------------
async def advance_inbox(
    db: AsyncSession, session_id: UUID, data: schemas.InboxAdvance, user_id: UUID
) -> SessionModel:
    session = await get_session(db, session_id, user_id)
    async with publication(db, session_id):
        await db.refresh(session, attribute_names=["inbox_handled_through_event_id", "tokens_used"])
        current = session.inbox_handled_through_event_id or 0
        if data.through_event_id < current:
            raise conflict(
                "CURSOR_BACKWARDS",
                f"The inbox of session {session_id} is handled through event {current}; "
                "the cursor only moves forward.",
            )
        newest = (
            await db.execute(
                text("SELECT max(event_id) FROM session_events WHERE session_id = :sid"),
                {"sid": str(session_id)},
            )
        ).scalar() or 0
        if data.through_event_id > newest:
            raise conflict(
                "CURSOR_AHEAD",
                f"Event {data.through_event_id} does not exist in session {session_id} yet "
                f"(newest is {newest}).",
                status_code=422,
            )
        session.inbox_handled_through_event_id = data.through_event_id
        session.tokens_used = session.tokens_used + data.tokens_used
    await db.refresh(session)
    return session


async def add_tokens(db: AsyncSession, session_id: UUID, tokens: int) -> None:
    """Add an attempt's or a turn's usage, inside the caller's transaction."""
    if tokens:
        await db.execute(
            text("UPDATE sessions SET tokens_used = tokens_used + :n WHERE session_id = :sid"),
            {"n": tokens, "sid": str(session_id)},
        )


# ----------------------------------------------------------------------
# The derived activity: never stored, published as `session_state`
# ----------------------------------------------------------------------
_ACTIVITY_FACTS = text(
    """
    SELECT s.control_state,
           COALESCE((SELECT json_agg(json_build_array(a.state, a.suspension ->> 'kind'))
                     FROM run_attempts a
                     WHERE a.session_id = s.session_id
                       AND run_attempt_state_is_active(a.state)), '[]'::json) AS active_attempts,
           EXISTS (SELECT 1 FROM node_runs r JOIN graph_nodes n ON n.node_id = r.node_id
                   WHERE r.session_id = s.session_id AND r.superseded_at IS NULL
                     AND n.removed_at IS NULL AND r.downstream_hold IS NOT NULL
                     AND r.hold_released_by_change_id IS NULL) AS active_hold,
           EXISTS (SELECT 1 FROM graph_changes c
                   WHERE c.session_id = s.session_id AND c.status = 'pending') AS pending_change
    FROM sessions s WHERE s.session_id = :sid
    """
)
# Current failed runs of live nodes, with their latest attempt and, for a
# resumable run, the unfinished tools of its latest checkpoint.
_FAILED_RUNS = text(
    """
    SELECT r.execution_policy, a.attempt_number, a.state, a.dispatched_at IS NOT NULL AS dispatched,
           (SELECT c.metadata -> 'pending_tools' FROM agent_checkpoints c
            WHERE c.run_id = r.run_id AND c.checkpoint_ns = ''
            ORDER BY c.created_at DESC, c.checkpoint_id DESC LIMIT 1) AS pending_tools
    FROM node_runs r
    JOIN graph_nodes n ON n.node_id = r.node_id AND n.removed_at IS NULL
    JOIN LATERAL (SELECT * FROM run_attempts x WHERE x.run_id = r.run_id
                  ORDER BY x.attempt_number DESC LIMIT 1) a ON true
    WHERE r.session_id = :sid AND r.superseded_at IS NULL AND r.status = 'failed'
    """
)


async def session_activity(db: AsyncSession, session_id: UUID) -> str:
    facts = (await db.execute(_ACTIVITY_FACTS, {"sid": str(session_id)})).one_or_none()
    if facts is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    failed = (await db.execute(_FAILED_RUNS, {"sid": str(session_id)})).all()
    stuck = any(
        not auto_retry_left(
            run_status="failed",
            latest=LatestAttempt(row.attempt_number, row.state, row.dispatched),
            policy=row.execution_policy,
            pending_tools=row.pending_tools,
        )
        for row in failed
    )
    return derive_activity(
        ActivityFacts(
            control_state=facts.control_state,
            active_attempts=tuple((state, kind) for state, kind in facts.active_attempts),
            active_hold=facts.active_hold,
            failed_without_auto_retry=stuck,
            pending_change=facts.pending_change,
        )
    )


async def publish_session_state(db: AsyncSession, session_id: UUID, publish: Publish) -> str:
    """Publish the activity as it stands inside the caller's transaction."""
    await db.flush()
    activity = await session_activity(db, session_id)
    publish("session_state", {"activity": activity})
    return activity


async def locked_control_state(db: AsyncSession, session_id: UUID) -> str:
    """The control state, read under the session lock."""
    await lock_session(db, session_id)
    return (
        await db.execute(
            text("SELECT control_state FROM sessions WHERE session_id = :sid"), {"sid": str(session_id)}
        )
    ).scalar_one()
