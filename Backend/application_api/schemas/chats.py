# schemas/chats.py
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ChatCreate(BaseModel):
    title: Optional[str] = None


class ChatUpdate(BaseModel):
    title: Optional[str] = None


class Chat(BaseModel):
    chat_id: UUID
    session_id: UUID
    title: Optional[str] = None
    created_at: datetime
    last_activity: datetime

    model_config = ConfigDict(from_attributes=True)
