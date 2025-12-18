from datetime import datetime
from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID
# --- Chat Message Schemas ---
class ChatMessageBase(BaseModel):
    role: str
    text: str
    extra_data: Optional[dict] = None

class ChatMessageCreate(ChatMessageBase):
    pass

class ChatMessage(ChatMessageBase):
    message_id: UUID
    chat_id: UUID
    timestamp: datetime

    class Config:
        orm_mode = True

