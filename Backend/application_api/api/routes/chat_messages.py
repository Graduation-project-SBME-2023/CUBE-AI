from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from application_api.db.session import get_db
from application_api.schemas.chat_messages import ChatMessageCreate, ChatMessage as ChatMessageRead
from application_api.repositories.chat_messages import (
    create_chat_message,
    get_chat_messages,
    get_chat_message,
    get_chat_messages_by_role
)

router = APIRouter(prefix="/chat-messages", tags=["Chat Messages"])


@router.post("/{chat_id}", response_model=ChatMessageRead)
async def api_create_chat_message(chat_id: UUID, message: ChatMessageCreate, db: AsyncSession = Depends(get_db)):
    return await create_chat_message(db, chat_id, message)


@router.get("/{chat_id}", response_model=List[ChatMessageRead])
async def api_get_chat_messages(
    chat_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await get_chat_messages(db, chat_id, skip=skip, limit=limit)


@router.get("/message/{message_id}", response_model=ChatMessageRead)
async def api_get_chat_message(message_id: UUID, db: AsyncSession = Depends(get_db)):
    return await get_chat_message(db, message_id)


@router.get("/{chat_id}/by_role", response_model=List[ChatMessageRead])
async def api_get_chat_messages_by_role(
    chat_id: UUID,
    role: str = Query(..., description="Role to filter, e.g., 'user', 'assistant'"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await get_chat_messages_by_role(db, chat_id, role, skip=skip, limit=limit)
