# schemas/chat_session.py
from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime

class ChatSessionBase(BaseModel):
    study_UID: str
    title: Optional[str] = None
    is_active: Optional[bool] = True
    summary: Optional[str] = None

class ChatSessionCreate(ChatSessionBase):
    pass

class ChatSessionUpdate(BaseModel):
    title: Optional[str] = None
    is_active: Optional[bool] = None
    summary: Optional[str] = None

class ChatSession(ChatSessionBase):
    chat_id: int
    started_at: datetime = None
    last_activity: datetime = None

    class Config:
        orm_mode = True
