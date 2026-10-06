"""Chat repository: the one chat of a session and input acceptance."""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.chat_messages import ChatMessage
from application_api.db.models.chats import Chat
from application_api.repositories.sessions import locked_control_state
from application_api.schemas.chats import InputAccept
from application_api.services.publication import conflict, publication

logger = logging.getLogger(__name__)


async def session_chat(db: AsyncSession, session_id: UUID) -> Chat:
    chat = (await db.execute(select(Chat).where(Chat.session_id == session_id))).scalar_one_or_none()
    if chat is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} has no chat")
    return chat


async def create_chat(db: AsyncSession, session_id: UUID, title: str | None = None) -> Chat:
    """A session has exactly one chat, created with it. Asking for another
    returns that one, so the frontend's `resolveChatId` keeps working."""
    return await session_chat(db, session_id)


async def accept_input(
    db: AsyncSession, session_id: UUID, data: InputAccept
) -> tuple[ChatMessage, bool]:
    """The message and its durable `user_input` event in one
    transaction. A replayed message id with the same text returns the stored row;
    with other text it is a 409. Returns the message and whether it is new."""
    async with publication(db, session_id) as publish:
        if await locked_control_state(db, session_id) == "archived":
            raise conflict("ARCHIVED", f"Session {session_id} is archived; it accepts no input.")
        chat = await session_chat(db, session_id)
        existing = (
            await db.execute(select(ChatMessage).where(ChatMessage.message_id == data.message_id))
        ).scalar_one_or_none()
        if existing is not None:
            if (existing.chat_id, existing.role, existing.text) != (chat.chat_id, "user", data.text):
                raise conflict(
                    "IDEMPOTENCY_CONFLICT",
                    f"Message {data.message_id} already exists with different content.",
                )
            return existing, False
        now = datetime.now(timezone.utc)
        message = ChatMessage(
            message_id=data.message_id, chat_id=chat.chat_id, role="user", text=data.text,
            metadata_=data.metadata, created_at=now,
        )
        db.add(message)
        chat.last_activity_at = now
        await db.flush()
        publish("user_input", {"chat_id": str(chat.chat_id), "message_id": str(message.message_id)})
    return message, True


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
