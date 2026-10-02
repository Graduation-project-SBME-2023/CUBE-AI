from sqlalchemy import CheckConstraint, Column, Integer, ForeignKey, String, DateTime, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from application_api.db.base import Base

class ChatMessage(Base):
    __tablename__ = "chat_messages"

    message_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text('gen_random_uuid()')
    )
    
    # Link to the owning chat, not the session. message_id is an
    # idempotency key: it is preserved across migration, and a retry with
    # conflicting content is a 409 rather than a second row.
    chat_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chats.chat_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Explicit fields for easy querying
    role = Column(String(50), nullable=False)  # e.g., 'user', 'assistant', 'system'
    text = Column(Text, nullable=False)        # actual message text

    # Optional extra data (`metadata` is reserved on declarative classes,
    # hence the attribute name).
    metadata_ = Column("metadata", JSONB, nullable=True)   # e.g., attachments or any future fields

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    chat = relationship("Chat", back_populates="messages")

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="role"),
        CheckConstraint("length(btrim(text)) > 0", name="text_not_blank"),
    )
