from sqlalchemy import Column, Integer, String, Boolean, Text, ForeignKey, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from core.database import Base  

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    chat_id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    study_UID = Column(Text, ForeignKey("studies.study_UID", ondelete="CASCADE"), nullable=False)
    title = Column(String(255))
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    last_activity = Column(DateTime(timezone=True))
    is_active = Column(Boolean, server_default="true")
    summary = Column(Text)

    # optional relationship (if Study model exists)
    # study = relationship("Study", back_populates="chat_sessions", lazy="joined")
