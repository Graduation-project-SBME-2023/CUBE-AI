from datetime import datetime
from pydantic import BaseModel
from typing import List, Optional

# --- Chat Message Schemas ---
class ChatMessageBase(BaseModel):
    role: str
    text: str
    extra_data: Optional[dict] = None

class ChatMessageCreate(ChatMessageBase):
    pass

class ChatMessage(ChatMessageBase):
    message_id: int
    chat_id: int
    timestamp: datetime

    class Config:
        orm_mode = True

