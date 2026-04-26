from sqlalchemy import Column, String, DateTime, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from core.database import Base


class WSLog(Base):
    __tablename__ = "ws_log"

    uuid = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()")
    )

    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.chat_id", ondelete="CASCADE"),
        nullable=False
    )

    msg_type = Column(String(255), nullable=True)

    payload = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # relationship back to ChatSession
    chat_session = relationship("ChatSession", back_populates="ws_logs")