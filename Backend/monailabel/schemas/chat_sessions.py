
# schemas/chat_session.py
from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional
from datetime import date, datetime
from uuid import UUID

        # from Backend.monailabel.models.chat_messages import ChatMessage
from .chat_messages import ChatMessage


class StudyRef(BaseModel):
    study_uid: str
    model_config = ConfigDict(from_attributes=True)

class ChatSessionBase(BaseModel):
    title: Optional[str] = None
    is_active: Optional[bool] = True
    summary: Optional[str] = None

class ChatSessionCreate(ChatSessionBase):
    study_uid: Optional[List[str]] = Field(default_factory=list)   # multiple studies

class ChatSessionUpdate(BaseModel):
    title: Optional[str] = None
    is_active: Optional[bool] = None
    summary: Optional[str] = None
    study_uid: Optional[List[str]] = None

class ChatSession(ChatSessionBase):
    chat_id: UUID
    started_at: datetime = None
    last_activity: Optional[datetime] = None
    studies: List[StudyRef] = []
    messages: List[ChatMessage] = []

    model_config = ConfigDict(from_attributes=True)


class ChatSessionSummary(ChatSessionBase):
    """Lightweight list item; message history is fetched separately."""

    chat_id: UUID
    started_at: datetime = None
    last_activity: Optional[datetime] = None
    studies: List[StudyRef] = []
    messages: List[ChatMessage] = []

    model_config = ConfigDict(from_attributes=True)
