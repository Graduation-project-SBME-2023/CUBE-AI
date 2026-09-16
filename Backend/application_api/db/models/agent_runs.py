from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from application_api.core.json_bounds import (
    AGENT_RUN_JSON_MAX_BYTES,
    COMPILED_PROMPT_MAX_BYTES,
)
from application_api.db.base import Base

# current. One execution of the agent a plan step selects.
AGENT_RUN_STATUSES = ("pending", "running", "completed", "failed")


class AgentRun(Base):
    """Execution state only — never a copy of the step definition."""

    __tablename__ = "agent_runs"

    agent_run_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    step_id = Column(
        UUID(as_uuid=True),
        ForeignKey("plan_steps.step_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    session_id = Column(UUID(as_uuid=True), nullable=False)

    # The compiler's exact output, assembled from the this flowLUS scratchpad
    # state and upstream results — not reconstructible, and therefore not a
    # copy of anything. Resolved immediately before the step starts and
    # immutable after that transition.
    compiled_prompt = Column(Text, nullable=True)
    input_refs = Column(JSONB(none_as_null=True), nullable=True)

    status = Column(String, nullable=False, server_default="pending")

    # file-elimination map: state/scratchpad.json and
    # outputs/agents/{agent_id}_output.json land here.
    durable_notes = Column(JSONB(none_as_null=True), nullable=True)
    warnings = Column(JSONB(none_as_null=True), nullable=True)
    output_summary = Column(JSONB(none_as_null=True), nullable=True)
    error = Column(JSONB(none_as_null=True), nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Two FK paths lead to plan_steps (the plain this flowK and the composite
    # (step_id, session_id) alignment FK); the relationship follows step_id.
    step = relationship("PlanStep", back_populates="agent_run", foreign_keys=[step_id])

    __table_args__ = (
        ForeignKeyConstraint(
            ["step_id", "session_id"],
            ["plan_steps.step_id", "plan_steps.session_id"],
            name="fk_agent_runs_step_id_session_id_plan_steps",
            ondelete="RESTRICT",
        ),
        # Supporting tuple: domain rows prove same-session alignment by
        # composite FK onto this.
        UniqueConstraint("agent_run_id", "session_id", name="uq_agent_runs_agent_run_id"),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="status",
        ),
        CheckConstraint(
            "(status = 'pending') = (started_at IS NULL)",
            name="started_iff_not_pending",
        ),
        CheckConstraint(
            "(status IN ('completed', 'failed')) = (completed_at IS NOT NULL)",
            name="completed_at_iff_terminal",
        ),
        CheckConstraint(
            "started_at IS NULL OR (compiled_prompt IS NOT NULL "
            "AND length(btrim(compiled_prompt)) > 0 AND input_refs IS NOT NULL)",
            name="compiled_before_start",
        ),
        CheckConstraint("status <> 'failed' OR error IS NOT NULL", name="failed_has_error"),
        CheckConstraint(
            "compiled_prompt IS NULL OR octet_length(compiled_prompt) <= "
            f"{COMPILED_PROMPT_MAX_BYTES}",
            name="compiled_prompt_bounded",
        ),
        CheckConstraint(
            " AND ".join(
                f"coalesce(pg_column_size({column}), 0) <= {AGENT_RUN_JSON_MAX_BYTES}"
                for column in (
                    "input_refs",
                    "durable_notes",
                    "warnings",
                    "output_summary",
                    "error",
                )
            ),
            name="json_bounded",
        ),
        Index("ix_agent_runs_session_id_status", "session_id", "status"),
    )
