"""Session event log"""

import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.Wslog import WSLog as WSLogModel
from application_api.schemas.Wslog import WSLogCreate
from application_api.services.publication import publication

logger = logging.getLogger(__name__)


async def create_ws_log(db: AsyncSession, data: WSLogCreate) -> WSLogModel:
    """Append one event, locking the session row first."""
    async with publication(db, data.session_id) as publish:
        event = publish(data.msg_type, data.payload)
    return event


async def get_ws_log(db: AsyncSession, event_id: int) -> WSLogModel:
    event = (
        await db.execute(select(WSLogModel).where(WSLogModel.event_id == event_id))
    ).scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="WS log not found")
    return event


async def replay_session(
    db: AsyncSession,
    session_id: UUID,
    after_event_id: int = 0,
    limit: int = 500,
) -> list[WSLogModel]:
    """`WHERE session_id =:id AND event_id >:cursor ORDER BY event_id`."""
    try:
        result = await db.execute(
            select(WSLogModel)
            .where(
                WSLogModel.session_id == session_id,
                WSLogModel.event_id > after_event_id,
            )
            .order_by(WSLogModel.event_id)
            .limit(limit)
        )
        return list(result.scalars().all())
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=str(e))


async def get_logs(
    db: AsyncSession,
    session_id: UUID | None = None,
    msg_type: str | None = None,
    after_event_id: int = 0,
    limit: int = 100,
) -> list[WSLogModel]:
    try:
        query = select(WSLogModel).where(WSLogModel.event_id > after_event_id)
        if session_id:
            query = query.where(WSLogModel.session_id == session_id)
        if msg_type:
            query = query.where(WSLogModel.msg_type == msg_type)
        result = await db.execute(query.order_by(WSLogModel.event_id).limit(limit))
        return list(result.scalars().all())
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=str(e))
