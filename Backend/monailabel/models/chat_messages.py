from sqlalchemy import Column, Integer, ForeignKey, String, DateTime, Text,UUID, text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from core.database import Base

class ChatMessage(Base):
    __tablename__ = "chat_messages"

    message_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text('gen_random_uuid()')
    )
    
    # Link to ChatSession
    chat_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.chat_id", ondelete="CASCADE"), nullable=False)

    # Explicit fields for easy querying
    role = Column(String(50), nullable=False)  # e.g., 'user', 'assistant', 'system'
    text = Column(Text, nullable=False)        # actual message text

    # Optional extra data
    extra_data = Column(JSON, nullable=True)   # e.g., attachments, metadata, or any future fields

    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship back to session
    chat_session = relationship("ChatSession", back_populates="messages")
