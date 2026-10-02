from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from application_api.core.json_bounds import SESSION_EVENT_PAYLOAD_MAX_BYTES
from application_api.db.base import Base


class SessionEvent(Base):
    """The session event log."""

    __tablename__ = "session_events"

    event_id = Column(
        BigInteger,
        Identity(always=True, cache=1),
        primary_key=True,
        autoincrement=True,
    )

    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="RESTRICT"),
        nullable=False,
    )

    event_type = Column(Text, nullable=False)
    payload = Column(JSONB, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session = relationship("Session", back_populates="events")

    __table_args__ = (
        CheckConstraint("length(btrim(event_type)) > 0", name="event_type_not_blank"),
        CheckConstraint(
            f"pg_column_size(payload) <= {SESSION_EVENT_PAYLOAD_MAX_BYTES}",
            name="payload_bounded",
        ),
        Index("ix_session_events_session_id_event_id", "session_id", "event_id"),
    )
