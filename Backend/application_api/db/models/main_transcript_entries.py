from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from application_api.db.base import Base


class MainTranscriptEntry(Base):
    """One entry of the Main's saved Claude Agent SDK session (D21), one
    session per CUBE.AI session; the Composer and the Recovery Agent are its
    subpaths. Not an execution table, so not fenced; written only by the
    session's own runtime and never a result."""

    __tablename__ = "main_transcript_entries"

    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    subpath = Column(Text, primary_key=True, server_default=text("''"))
    seq = Column(BigInteger, Identity(always=True), primary_key=True)
    entry_uuid = Column(Text, nullable=False)
    entry = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "subpath",
            "entry_uuid",
            name="uq_main_transcript_entries_session_id_subpath_entry_uuid",
        ),
        CheckConstraint("length(btrim(entry_uuid)) > 0", name="entry_uuid_not_blank"),
        CheckConstraint(
            "jsonb_typeof(entry) = 'object' AND pg_column_size(entry) <= 4194304", name="entry_bounded"
        ),
    )
