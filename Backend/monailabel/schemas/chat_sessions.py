# schemas/chat_session.py
from pydantic import BaseModel
from typing import Optional

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
    started_at: Optional[str] = None
    last_activity: Optional[str] = None

    class Config:
        orm_mode = True
