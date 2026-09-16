from sqlalchemy import Column, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from application_api.db.base import Base


class Chat(Base):
    """One conversation inside a session."""

    __tablename__ = "chats"

    chat_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    title = Column(String(255))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # REJECTED the removal of this column: it has a dedicated TOUCH
    # endpoint and eight readers in ChatSidebar.tsx. It is not derived from the
    # message timeline at read time.
    last_activity = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    session = relationship("Session", back_populates="chats")
    messages = relationship("ChatMessage", back_populates="chat", lazy="selectin")
