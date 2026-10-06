from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from application_api.db.base import Base


class AgentCheckpoint(Base):
    """One LangGraph checkpoint of a resumable agent node. The thread is
    the run (`thread_id = run_id`). Reasoning state, never a result."""

    __tablename__ = "agent_checkpoints"

    run_id = Column(UUID(as_uuid=True), primary_key=True)
    checkpoint_ns = Column(Text, primary_key=True, server_default=text("''"))
    checkpoint_id = Column(Text, primary_key=True)
    session_id = Column(UUID(as_uuid=True), nullable=False)
    parent_checkpoint_id = Column(Text, nullable=True)
    # The writer: the run's current attempt (fenced).
    attempt_id = Column(UUID(as_uuid=True), nullable=False)
    # Serializer output, opaque to the Application API.
    type = Column(Text, nullable=False)
    checkpoint = Column(LargeBinary, nullable=False)
    # The only part the database reads: engine_version, spec_digest, step,
    # pending_tools [{name, idempotent}], seeded_from?
    metadata_ = Column("metadata", JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "session_id"],
            ["node_runs.run_id", "node_runs.session_id"],
            name="fk_agent_checkpoints_run_id_session_id_node_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "run_id"],
            ["run_attempts.attempt_id", "run_attempts.run_id"],
            name="fk_agent_checkpoints_attempt_id_run_id_run_attempts",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(btrim(checkpoint_id)) > 0", name="checkpoint_id_not_blank"),
        CheckConstraint(
            "jsonb_typeof(metadata) = 'object' AND metadata ? 'v' AND metadata ? 'spec_digest' "
            "AND metadata ? 'engine_version' AND pg_column_size(metadata) <= 1048576",
            name="metadata_versioned",
        ),
        Index(
            "ix_agent_checkpoints_run_id_checkpoint_ns_created_at",
            "run_id",
            "checkpoint_ns",
            text("created_at DESC"),
        ),
    )


class AgentCheckpointWrite(Base):
    """A pending write of a checkpoint: a finished task or fan-out branch of a
    step that did not complete. A replayed key is a no-op."""

    __tablename__ = "agent_checkpoint_writes"

    run_id = Column(UUID(as_uuid=True), primary_key=True)
    checkpoint_ns = Column(Text, primary_key=True, server_default=text("''"))
    checkpoint_id = Column(Text, primary_key=True)
    task_id = Column(Text, primary_key=True)
    idx = Column(Integer, primary_key=True)
    session_id = Column(UUID(as_uuid=True), nullable=False)
    channel = Column(Text, nullable=False)
    type = Column(Text, nullable=True)
    value = Column(LargeBinary, nullable=False)
    attempt_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "checkpoint_ns", "checkpoint_id"],
            [
                "agent_checkpoints.run_id",
                "agent_checkpoints.checkpoint_ns",
                "agent_checkpoints.checkpoint_id",
            ],
            name="fk_agent_checkpoint_writes_checkpoint",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["run_id", "session_id"],
            ["node_runs.run_id", "node_runs.session_id"],
            name="fk_agent_checkpoint_writes_run_id_session_id_node_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "run_id"],
            ["run_attempts.attempt_id", "run_attempts.run_id"],
            name="fk_agent_checkpoint_writes_attempt_id_run_id_run_attempts",
            ondelete="RESTRICT",
        ),
    )
