"""Chat repository"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.chats import Chat
from application_api.db.models.sessions import Session as SessionModel

logger = logging.getLogger(__name__)


async def create_chat(db: AsyncSession, session_id: UUID, title: str | None = None) -> Chat:
    session_exists = (
        await db.execute(
            select(SessionModel.session_id).where(
                SessionModel.session_id == session_id
            )
        )
    ).scalar_one_or_none()
    if session_exists is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    now = datetime.now(timezone.utc)
    chat = Chat(session_id=session_id, title=title, created_at=now, last_activity_at=now)
    try:
        db.add(chat)
        await db.commit()
        await db.refresh(chat)
        return chat
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def get_chat(db: AsyncSession, chat_id: UUID) -> Chat:
    chat = (
        await db.execute(select(Chat).where(Chat.chat_id == chat_id))
    ).scalar_one_or_none()
    if chat is None:
        raise HTTPException(status_code=404, detail=f"Chat {chat_id} not found")
    return chat


async def get_chats_for_session(
    db: AsyncSession, session_id: UUID, skip: int = 0, limit: int = 100
) -> list[Chat]:
    result = await db.execute(
        select(Chat)
        .where(Chat.session_id == session_id)
        .order_by(Chat.last_activity_at.desc(), Chat.chat_id)
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_chats(db: AsyncSession, skip: int = 0, limit: int = 100) -> list[Chat]:
    result = await db.execute(
        select(Chat)
        .order_by(Chat.last_activity_at.desc(), Chat.chat_id)
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


async def update_chat(db: AsyncSession, chat_id: UUID, title: str | None) -> Chat:
    chat = await get_chat(db, chat_id)
    if title is not None:
        chat.title = title
    chat.last_activity_at = datetime.now(timezone.utc)
    try:
        await db.commit()
        await db.refresh(chat)
        return chat
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def touch_chat(db: AsyncSession, chat_id: UUID) -> Chat:
    """Dedicated TOUCH: `last_activity_at` is stored, not derived at read time."""
    chat = await get_chat(db, chat_id)
    chat.last_activity_at = datetime.now(timezone.utc)
    try:
        await db.commit()
        await db.refresh(chat)
        return chat
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def delete_chat(db: AsyncSession, chat_id: UUID):
    chat = await get_chat(db, chat_id)
    try:
        await db.delete(chat)
        await db.commit()
        return {"detail": f"Chat {chat_id} deleted successfully"}
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"Chat {chat_id} still has messages. Message history is not "
                "deleted implicitly."
            ),
        )
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete chat: {str(e)}")
