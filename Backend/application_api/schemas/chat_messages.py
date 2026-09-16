from datetime import datetime
from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from uuid import UUID

# --- Chat Message Schemas ---
class ChatMessageBase(BaseModel):
    role: str
    text: str
    extra_data: Optional[dict] = None

class ChatMessageCreate(ChatMessageBase):
    # Message ids are idempotency keys. A client that retries
    # after a timeout sends the same id and gets the same row back; a retry
    # with the same id and different content is a 409, never a second message
    # and never a silent overwrite.
    message_id: Optional[UUID] = None

class ChatMessage(ChatMessageBase):
    message_id: UUID
    chat_id: UUID
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)
