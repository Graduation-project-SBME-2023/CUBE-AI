# schemas/chats.py
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChatCreate(BaseModel):
    title: Optional[str] = None


class ChatUpdate(BaseModel):
    title: Optional[str] = None


class Chat(BaseModel):
    chat_id: UUID
    session_id: UUID
    title: Optional[str] = None
    created_at: datetime
    last_activity_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InputAccept(BaseModel):
    """POST /chats/session/{id}/input. `message_id` is the idempotency key."""

    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    text: str = Field(min_length=1, max_length=100_000)
    metadata: Optional[dict[str, Any]] = None
