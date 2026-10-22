from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from application_api.db.base import Base


class AgentTranscriptEntry(Base):
    """One entry of an agent node's Claude Agent SDK session (D27). The
    session is the run (`session_id = run_id`); `subpath` is '' for the main
    transcript and the SDK's opaque subpath for a subagent. Reasoning state,
    never a result: stored and returned unchanged, never parsed here."""

    __tablename__ = "agent_transcript_entries"

    run_id = Column(UUID(as_uuid=True), primary_key=True)
    subpath = Column(Text, primary_key=True, server_default=text("''"))
    # The load order, assigned on insert.
    seq = Column(BigInteger, Identity(always=True), primary_key=True)
    session_id = Column(UUID(as_uuid=True), nullable=False)
    # The writer: the run's latest running or suspended attempt (fenced).
    attempt_id = Column(UUID(as_uuid=True), nullable=False)
    # The SDK's entry uuid: a re-delivered batch is a no-op.
    entry_uuid = Column(Text, nullable=False)
    entry = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "run_id", "subpath", "entry_uuid", name="uq_agent_transcript_entries_run_id_subpath_entry_uuid"
        ),
        ForeignKeyConstraint(
            ["run_id", "session_id"],
            ["node_runs.run_id", "node_runs.session_id"],
            name="fk_agent_transcript_entries_run_id_session_id_node_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "run_id"],
            ["run_attempts.attempt_id", "run_attempts.run_id"],
            name="fk_agent_transcript_entries_attempt_id_run_id_run_attempts",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(btrim(entry_uuid)) > 0", name="entry_uuid_not_blank"),
        CheckConstraint(
            "jsonb_typeof(entry) = 'object' AND pg_column_size(entry) <= 4194304", name="entry_bounded"
        ),
    )
