from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, ForeignKey, Identity, Index, String, Text, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from application_api.db.base import Base

class ChatMessage(Base):
    __tablename__ = "chat_messages"

    message_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text('gen_random_uuid()')
    )
    
    # Link to the owning chat, not the session. message_id is an
    # idempotency key: it is preserved across migration, and a retry with
    # conflicting content is a 409 rather than a second row.
    chat_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chats.chat_id", ondelete="RESTRICT"),
        nullable=False,
    )

    # The total transcript order: rows written in one transaction share
    # created_at, so a call and its result are ordered by seq alone.
    seq = Column(BigInteger, Identity(always=True), nullable=False, unique=True)

    role = Column(String(50), nullable=False)  # user | assistant | system | tool
    # A tool row's text is a short label; its payload lives in metadata.
    text = Column(Text, nullable=False)

    # What the user sees: the user's messages and the Main's replies, with an
    # optional `ui` view hint. The Main's tool calls and results live in its SDK
    # transcript (`main_transcript_entries`, D21); older `tool` rows stay as
    # history. `metadata` is reserved on declarative classes, hence the name.
    metadata_ = Column("metadata", JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    chat = relationship("Chat", back_populates="messages")

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system', 'tool')", name="role"),
        CheckConstraint("length(btrim(text)) > 0", name="text_not_blank"),
        Index("ix_chat_messages_chat_id_seq", "chat_id", "seq"),
        Index("ix_chat_messages_chat_id_created_at", "chat_id", "created_at", "message_id"),
    )
