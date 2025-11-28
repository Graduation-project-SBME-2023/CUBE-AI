from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from core.database import Base  
from .chat_link_study import chat_study_association

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    chat_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
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

