from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException
from sqlalchemy.future import select

from models.chat_messages import ChatMessage
from models.chat_sessions import ChatSession
from schemas.chat_messages import ChatMessageCreate
from uuid import UUID

# -------------------------
# CREATE MESSAGE
# -------------------------
async def create_chat_message(db: AsyncSession, chat_id: UUID, message: ChatMessageCreate):
    try:
        # Ensure chat session exists
        result = await db.execute(select(ChatSession).filter(ChatSession.chat_id == chat_id))
        session = result.scalars().first()
        if not session:
            raise HTTPException(status_code=404, detail="Chat session not found")

        db_msg = ChatMessage(
            chat_id=chat_id,
            role=message.role,
            text=message.text,
            extra_data=message.extra_data
        )
        db.add(db_msg)
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
        result = await db.execute(select(ChatSession).filter(ChatSession.chat_id == chat_id))
        session = result.scalars().first()
        if not session:
            raise HTTPException(status_code=404, detail="Chat session not found")

        result = await db.execute(
            select(ChatMessage)
            .filter(ChatMessage.chat_id == chat_id)
            .order_by(ChatMessage.timestamp.asc())
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
# UPDATE MESSAGE
# -------------------------
async def update_chat_message(db: AsyncSession, message_id: UUID, new_data: ChatMessageCreate):
    try:
        result = await db.execute(select(ChatMessage).filter(ChatMessage.message_id == message_id))
        msg = result.scalars().first()
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")

        msg.role = new_data.role
        msg.text = new_data.text
        msg.extra_data = new_data.extra_data
        await db.commit()
        await db.refresh(msg)
        return msg
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update message: {str(e)}")


# -------------------------
# DELETE MESSAGE
# -------------------------
async def delete_chat_message(db: AsyncSession, message_id: UUID):
    try:
        result = await db.execute(select(ChatMessage).filter(ChatMessage.message_id == message_id))
        msg = result.scalars().first()
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")
        await db.delete(msg)
        await db.commit()
        return {"detail": "Message deleted"}
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete message: {str(e)}")


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
            .order_by(ChatMessage.timestamp.asc())
            .offset(skip)
            .limit(limit)
        )
        messages = result.scalars().all()
        if not messages:
            raise HTTPException(status_code=404, detail=f"No messages found for role '{role}'")
        return messages
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch messages by role: {str(e)}")
