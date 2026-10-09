"""Session event log"""

import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.session_events import SessionEvent
from application_api.repositories.users import owned_session_ids
from application_api.schemas.session_events import SessionEventCreate
from application_api.services.publication import publication

logger = logging.getLogger(__name__)


async def create_session_event(db: AsyncSession, data: SessionEventCreate) -> SessionEvent:
    """Append one event, locking the session row first."""
    async with publication(db, data.session_id) as publish:
        event = publish(data.event_type, data.payload)
    return event


async def get_session_event(db: AsyncSession, event_id: int) -> SessionEvent:
    event = (
        await db.execute(select(SessionEvent).where(SessionEvent.event_id == event_id))
    ).scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Session event not found")
    return event


async def replay_session(
    db: AsyncSession,
    session_id: UUID,
    after_event_id: int = 0,
    limit: int = 500,
) -> list[SessionEvent]:
    """`WHERE session_id =:id AND event_id >:cursor ORDER BY event_id`."""
    try:
        result = await db.execute(
            select(SessionEvent)
            .where(
                SessionEvent.session_id == session_id,
                SessionEvent.event_id > after_event_id,
            )
            .order_by(SessionEvent.event_id)
            .limit(limit)
        )
        return list(result.scalars().all())
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=str(e))


async def get_events(
    db: AsyncSession,
    session_id: UUID | None = None,
    event_type: str | None = None,
    after_event_id: int = 0,
    limit: int = 100,
    owner: UUID | None = None,
) -> list[SessionEvent]:
    try:
        query = select(SessionEvent).where(SessionEvent.event_id > after_event_id)
        if owner is not None:
            query = query.where(SessionEvent.session_id.in_(owned_session_ids(owner)))
        if session_id:
            query = query.where(SessionEvent.session_id == session_id)
        if event_type:
            query = query.where(SessionEvent.event_type == event_type)
        result = await db.execute(query.order_by(SessionEvent.event_id).limit(limit))
        return list(result.scalars().all())
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=str(e))
