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

from application_api.core.json_bounds import WS_LOG_PAYLOAD_MAX_BYTES
from application_api.db.base import Base


class WSLog(Base):
    """The session event log."""

    __tablename__ = "ws_log"

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

    msg_type = Column(Text, nullable=False)
    payload = Column(JSONB, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session = relationship("Session", back_populates="ws_logs")

    __table_args__ = (
        CheckConstraint("length(btrim(msg_type)) > 0", name="msg_type_not_blank"),
        CheckConstraint(
            f"pg_column_size(payload) <= {WS_LOG_PAYLOAD_MAX_BYTES}",
            name="payload_bounded",
        ),
        Index("ix_ws_log_session_id_event_id", "session_id", "event_id"),
    )
