from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from application_api.db.base import Base 
from .chat_messages import ChatMessage
from .Wslog import WSLog 
from .hypothesis import Hypothesis
from .chat_link_study import chat_study_association
from sqlalchemy.dialects.postgresql import UUID
class ChatSession(Base):
    __tablename__ = "chat_sessions"

    chat_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text('gen_random_uuid()')
    )
    title = Column(String(255))
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    last_activity = Column(DateTime(timezone=True))
    is_active = Column(Boolean, server_default="true")
    summary = Column(Text)

    studies = relationship(
        "Study",  # ← string reference
        secondary=chat_study_association,
        back_populates="chat_sessions",
        lazy="joined"
    )
    messages = relationship("ChatMessage", back_populates="chat_session", lazy="selectin",cascade="all, delete-orphan")

    hypothesis = relationship("Hypothesis", back_populates="chat_session", uselist=False, cascade="all, delete-orphan")
    ws_logs = relationship(
    "WSLog",
    back_populates="chat_session",
    cascade="all, delete-orphan",
    lazy="selectin"
)