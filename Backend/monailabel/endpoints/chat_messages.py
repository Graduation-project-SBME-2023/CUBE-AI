from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from schemas.chat_messages import ChatMessageCreate, ChatMessage as ChatMessageRead
from crud.chat_messages import (
    create_chat_message,
    get_chat_messages,
    get_chat_message,
    update_chat_message,
    delete_chat_message,
    get_chat_messages_by_role
)

router = APIRouter(prefix="/chat_messages", tags=["Chat Messages"])


@router.post("/{chat_id}", response_model=ChatMessageRead)
async def api_create_chat_message(chat_id: int, message: ChatMessageCreate, db: AsyncSession = Depends(get_db)):
    return await create_chat_message(db, chat_id, message)


@router.get("/{chat_id}", response_model=List[ChatMessageRead])
async def api_get_chat_messages(chat_id: int, db: AsyncSession = Depends(get_db)):
    return await get_chat_messages(db, chat_id)


@router.get("/message/{message_id}", response_model=ChatMessageRead)
async def api_get_chat_message(message_id: int, db: AsyncSession = Depends(get_db)):
    return await get_chat_message(db, message_id)


@router.put("/message/{message_id}", response_model=ChatMessageRead)
async def api_update_chat_message(message_id: int, new_data: ChatMessageCreate, db: AsyncSession = Depends(get_db)):
    return await update_chat_message(db, message_id, new_data)


@router.delete("/message/{message_id}")
async def api_delete_chat_message(message_id: int, db: AsyncSession = Depends(get_db)):
    return await delete_chat_message(db, message_id)


@router.get("/{chat_id}/by_role", response_model=List[ChatMessageRead])
async def api_get_chat_messages_by_role(chat_id: int, role: str = Query(..., description="Role to filter, e.g., 'user', 'assistant'"), db: AsyncSession = Depends(get_db)):
    return await get_chat_messages_by_role(db, chat_id, role)