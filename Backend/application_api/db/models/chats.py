from sqlalchemy import Column, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from application_api.db.base import Base


class Chat(Base):
    """The one conversation of a session: created with it."""

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
    last_activity_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session = relationship("Session", back_populates="chats")
    # Paged by the repositories in `seq` order; never loaded implicitly.
    messages = relationship("ChatMessage", back_populates="chat", lazy="raise")

    # One chat per session; dropping this is the only change many chats need.
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_chats_session_id"),
        Index("ix_chats_last_activity_at", text("last_activity_at DESC")),
    )
