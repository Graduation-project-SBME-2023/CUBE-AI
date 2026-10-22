from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from application_api.db.base import Base

# Non-terminal: the attempt may still be doing work (or, when suspended, may
# resume). `unknown` blocks every start in the session until a reconcile.
ACTIVE_ATTEMPT_STATES = ("preparing", "submitted", "running", "unknown", "suspended")
# Terminal only on evidence that the work stopped.
TERMINAL_ATTEMPT_STATES = ("succeeded", "failed", "cancelled", "interrupted")
# Attempts that hold (or may hold) a live process.
WORKING_ATTEMPT_STATES = ("preparing", "submitted", "running")
DISPATCH_KINDS = ("in_process", "job", "inline")
# Suspensions that wait for the user's answer; a pause waits for the session.
QUESTION_KINDS = ("ask_user", "tool_approval")
SUSPENSION_KINDS = (*QUESTION_KINDS, "pause")
# The error codes a confirmed stop keeps as a failure rather than a cancellation.
TIMEOUT_CODES = ("timeout", "gate_timeout")


def _states(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


class RunAttempt(Base):
    """One real execution try of a run, persisted before dispatch."""

    __tablename__ = "run_attempts"

    # The fencing token every execution write presents.
    attempt_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    run_id = Column(UUID(as_uuid=True), nullable=False)
    session_id = Column(UUID(as_uuid=True), nullable=False)
    attempt_number = Column(SmallInteger, nullable=False)
    dispatch_kind = Column(Text, nullable=False)
    # Job attempts: `{job_id}.{attempt_number}`, the job's queueing lock and the
    # name of its job dir. Set once, before dispatch; reused on resubmission.
    submission_key = Column(Text, nullable=True)
    request = Column(JSONB(none_as_null=True), nullable=True)
    state = Column(Text, nullable=False)
    # {v: 2, kind: ask_user|tool_approval|pause, tool_use_id?, form?, payload, since, resume?}.
    suspension = Column(JSONB(none_as_null=True), nullable=True)
    # {v: 1, calls: [{tool_use_id, tool, idempotent, started_at}]}: the calls the
    # attempt is running, so a retry after a crash knows whether it is safe (T4 rule f).
    pending_tools = Column(JSONB(none_as_null=True), nullable=True)
    resumed_at = Column(DateTime(timezone=True), nullable=True)
    dispatched_at = Column(DateTime(timezone=True), nullable=True)
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
    error = Column(JSONB(none_as_null=True), nullable=True)
    usage = Column(JSONB(none_as_null=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    run = relationship("NodeRun", back_populates="attempts")

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "session_id"],
            ["node_runs.run_id", "node_runs.session_id"],
            name="fk_run_attempts_run_id_session_id_node_runs",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "attempt_number", name="uq_run_attempts_run_id_attempt_number"),
        UniqueConstraint("attempt_id", "run_id", name="uq_run_attempts_attempt_id_run_id"),
        CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        CheckConstraint(
            "dispatch_kind IN ('in_process', 'job', 'inline', 'legacy')", name="dispatch_kind"
        ),
        CheckConstraint(
            f"state IN {_states(ACTIVE_ATTEMPT_STATES + TERMINAL_ATTEMPT_STATES)}", name="state"
        ),
        CheckConstraint(
            f"(state IN {_states(TERMINAL_ATTEMPT_STATES)}) = (ended_at IS NOT NULL)",
            name="ended_at_iff_terminal",
        ),
        CheckConstraint(
            "state NOT IN ('submitted', 'running', 'succeeded', 'suspended') "
            "OR dispatched_at IS NOT NULL",
            name="dispatched_before_running",
        ),
        CheckConstraint("state <> 'failed' OR error ? 'code'", name="failed_has_error_code"),
        CheckConstraint(
            "state <> 'suspended' OR (dispatch_kind = 'in_process' AND suspension ? 'kind')",
            name="suspension_shape",
        ),
        CheckConstraint(
            "submission_key IS NULL OR length(btrim(submission_key)) > 0",
            name="submission_key_not_blank",
        ),
        Index(
            "uq_run_attempts_submission_key",
            "submission_key",
            unique=True,
            postgresql_where=text("submission_key IS NOT NULL"),
        ),
        # One non-terminal attempt per run, and (for now) per session.
        Index(
            "uq_run_attempts_one_active_per_run",
            "run_id",
            unique=True,
            postgresql_where=text(f"state IN {_states(ACTIVE_ATTEMPT_STATES)}"),
        ),
        Index(
            "uq_run_attempts_one_active_per_session",
            "session_id",
            unique=True,
            postgresql_where=text(f"state IN {_states(ACTIVE_ATTEMPT_STATES)}"),
        ),
        Index("ix_run_attempts_suspended", "state", postgresql_where=text("state = 'suspended'")),
    )
