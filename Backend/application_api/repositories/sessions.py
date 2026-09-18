"""Session repository"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from application_api.db.models.plan import Plan
from application_api.db.models.sessions import Session as SessionModel
from application_api.schemas import sessions as schemas

logger = logging.getLogger(__name__)


async def create_session(db: AsyncSession, data: schemas.SessionCreate) -> SessionModel:
    try:
        now = datetime.now(timezone.utc)
        session = SessionModel(
            title=data.title,
            prompt=data.prompt,
            prompt_kind=data.prompt_kind,
            status="planning",
            created_at=now,
            updated_at=now,
        )
        db.add(session)
        await db.flush()

        # The session and its empty plan are created in ONE transaction.
        # A deferred constraint trigger rejects a committed session with no
        # plan, so this is not optional and cannot be deferred to a later call.
        db.add(Plan(session_id=session.session_id, approved_at=None, updated_at=now))

        await db.commit()
        await db.refresh(session)
        return session
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def get_session(db: AsyncSession, session_id: UUID) -> SessionModel:
    try:
        result = await db.execute(
            select(SessionModel).where(SessionModel.session_id == session_id)
        )
        session = result.unique().scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        return session
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def get_sessions(
    db: AsyncSession,
    status: str | None = None,
    include_archived: bool = False,
    skip: int = 0,
    limit: int = 50,
):
    try:
        query = select(SessionModel).options(
            noload(SessionModel.chats),
            noload(SessionModel.ws_logs),
        )
        if status:
            query = query.where(SessionModel.status == status)
        elif not include_archived:
            query = query.where(SessionModel.status != "archived")

        query = (
            query.order_by(SessionModel.updated_at.desc().nullslast())
            .offset(skip)
            .limit(limit)
        )
        result = await db.execute(query)
        return list(result.unique().scalars().all())
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def update_session(
    db: AsyncSession, session_id: UUID, data: schemas.SessionUpdate
) -> SessionModel:
    session = await get_session(db, session_id)

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(session, field, value)

    try:
        await db.commit()
        await db.refresh(session)
        return session
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# Durable pause, expressed as guarded transitions rather than a flag. Each
# entry is the set of statuses a transition may start from; anything else is a
# 409 rather than a silent overwrite.
_ALLOWED_TRANSITIONS = {
    "planning": {"planning"},
    "ready": {"planning", "ready", "paused"},
    "running": {"ready", "paused", "running"},
    "pausing": {"running"},
    "paused": {"pausing"},
    "completed": {"running"},
    "failed": {"planning", "ready", "running", "pausing", "paused"},
    "archived": set(),  # any status may be archived
}


async def set_session_status(
    db: AsyncSession, session_id: UUID, new_status: str
) -> SessionModel:
    session = await get_session(db, session_id)
    allowed = _ALLOWED_TRANSITIONS.get(new_status)
    if allowed is None:
        raise HTTPException(status_code=400, detail=f"Unknown status {new_status!r}")
    if allowed and session.status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Session {session_id} is {session.status!r}; it cannot move to "
                f"{new_status!r}."
            ),
        )
    session.status = new_status
    try:
        await db.commit()
        await db.refresh(session)
        return session
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def archive_session(db: AsyncSession, session_id: UUID) -> SessionModel:
    """Normal removal. This is what a delete request means in current."""
    return await set_session_status(db, session_id, "archived")


async def delete_session(db: AsyncSession, session_id: UUID):
    """Hard delete — refused while provenance still references the session.: normal removal is `status='archived'`; a hard delete is an admin operation under backup and retained-reference checks. The FKs are RESTRICT, so the database refuses too; this turns that into a usable 409."""
    session = await get_session(db, session_id)
    try:
        await db.delete(session)
        await db.commit()
        return {"detail": f"Session {session_id} deleted successfully"}
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"Session {session_id} still has messages, events or a plan "
                "referencing it. Archive it instead of deleting it."
            ),
        )
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Failed to delete session: {str(e)}"
        )
