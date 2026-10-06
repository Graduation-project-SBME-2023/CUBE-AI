from datetime import datetime
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from typing import List, Optional
from uuid import UUID

# --- Chat Message Schemas ---
class ChatMessageBase(BaseModel):
    role: str
    text: str
    # The ORM attribute is `metadata_` (`metadata` is reserved on declarative
    # classes); the JSON field is `metadata`.
    metadata: Optional[dict] = Field(
        default=None,
        validation_alias=AliasChoices("metadata_", "metadata"),
        serialization_alias="metadata",
    )

class ChatMessageCreate(ChatMessageBase):
    # Message ids are idempotency keys. A client that retries
    # after a timeout sends the same id and gets the same row back; a retry
    # with the same id and different content is a 409, never a second message
    # and never a silent overwrite.
    message_id: Optional[UUID] = None

class ChatMessage(ChatMessageBase):
    message_id: UUID
    chat_id: UUID
    # The total transcript order.
    seq: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
