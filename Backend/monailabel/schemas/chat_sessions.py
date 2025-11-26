
# schemas/chat_session.py
from pydantic import BaseModel
from typing import List, Optional
from datetime import date, datetime

class StudyRef(BaseModel):
    study_uid: str
    class Config:
        orm_mode = True
class ChatSessionBase(BaseModel):
    title: Optional[str] = None
    is_active: Optional[bool] = True
    summary: Optional[str] = None

class ChatSessionCreate(ChatSessionBase):
    study_uid: Optional[List[str]] = []   # multiple studies
class ChatSessionUpdate(BaseModel):
    title: Optional[str] = None
    is_active: Optional[bool] = None
    summary: Optional[str] = None
    study_uid: Optional[List[str]] = None

class ChatSession(ChatSessionBase):
    chat_id: int
    started_at: datetime = None
    last_activity: datetime = None
    studies: List[StudyRef] = []
    class Config:
        orm_mode = True
