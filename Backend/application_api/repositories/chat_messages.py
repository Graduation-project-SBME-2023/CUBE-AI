from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException
from sqlalchemy.future import select

from application_api.db.models.chat_messages import ChatMessage
from application_api.db.models.chats import Chat
from application_api.schemas.chat_messages import ChatMessageCreate
from uuid import UUID

# -------------------------
# CREATE MESSAGE
# -------------------------
async def create_chat_message(db: AsyncSession, chat_id: UUID, message: ChatMessageCreate):
    try:
        # Ensure chat session exists
        result = await db.execute(select(Chat).filter(Chat.chat_id == chat_id))
        session = result.scalars().first()
        if not session:
            raise HTTPException(status_code=404, detail="Chat session not found")

        if message.message_id is not None:
            existing = (
                await db.execute(
                    select(ChatMessage).filter(
                        ChatMessage.message_id == message.message_id
                    )
                )
            ).scalars().first()
            if existing is not None:
                same = (
                    existing.chat_id == chat_id
                    and existing.role == message.role
                    and existing.text == message.text
                )
                if same:
                    # Idempotent replay of a retried send.
                    return existing
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Message {message.message_id} already exists with "
                        "different content. A message id is an idempotency key, "
                        "not a slot to overwrite."
                    ),
                )

        db_msg = ChatMessage(
            chat_id=chat_id,
            role=message.role,
            text=message.text,
            metadata_=message.metadata,
        )
        if message.message_id is not None:
            db_msg.message_id = message.message_id
        db.add(db_msg)

        # The chat owns last_activity_at (it has a dedicated TOUCH endpoint and
        # eight frontend readers), so a new message advances it here rather
        # than leaving the UI to guess from the message timeline.
        session.last_activity_at = datetime.now(timezone.utc)
        db.add(session)

        await db.commit()
        await db.refresh(db_msg)
        return db_msg
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create chat message: {str(e)}")


# -------------------------
# GET ALL MESSAGES IN SESSION
# -------------------------
async def get_chat_messages(db: AsyncSession, chat_id: UUID, skip: int = 0, limit: int = 50):
    try:
        # Check session exists
        result = await db.execute(select(Chat).filter(Chat.chat_id == chat_id))
        session = result.scalars().first()
        if not session:
            raise HTTPException(status_code=404, detail="Chat session not found")

        result = await db.execute(
            select(ChatMessage)
            .filter(ChatMessage.chat_id == chat_id)
            .order_by(ChatMessage.seq.asc())
            .offset(skip)
            .limit(limit)
        )
        messages = result.scalars().all()
        return messages
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch chat messages: {str(e)}")


# -------------------------
# GET SINGLE MESSAGE BY ID
# -------------------------
async def get_chat_message(db: AsyncSession, message_id: UUID):
    try:
        result = await db.execute(select(ChatMessage).filter(ChatMessage.message_id == message_id))
        msg = result.scalars().first()
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")
        return msg
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch message: {str(e)}")


# -------------------------
# GET MESSAGES BY ROLE
# -------------------------
async def get_chat_messages_by_role(
    db: AsyncSession, chat_id: UUID, role: str, skip: int = 0, limit: int = 50
):
    try:
        result = await db.execute(
            select(ChatMessage)
            .filter(ChatMessage.chat_id == chat_id, ChatMessage.role == role)
            .order_by(ChatMessage.seq.asc())
            .offset(skip)
            .limit(limit)
        )
        messages = result.scalars().all()
        if not messages:
            raise HTTPException(status_code=404, detail=f"No messages found for role '{role}'")
        return messages
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch messages by role: {str(e)}")
