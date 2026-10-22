from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from application_api.core.json_bounds import (
    COMPILED_PROMPT_MAX_BYTES,
    NODE_RUN_JSON_MAX_BYTES,
)
from application_api.db.base import Base

# A projection of the run's attempts. Runs are created
# started, so there is no `pending`.
NODE_RUN_STATUSES = ("running", "completed", "failed", "cancelled")
TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled")
EXPECTATION_VIOLATED = "expectation_violated"
# An `approval: after` node's completed run holds the next node for the user (D28).
USER_REVIEW = "user_review"


class NodeRun(Base):
    """One execution of a node's accepted definition, agent or tool.
    Created when the node starts, together with its attempt 1."""

    __tablename__ = "node_runs"

    run_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    # No standalone FK: the composite (node_id, session_id) FK proves existence
    # and same-session ownership together.
    node_id = Column(UUID(as_uuid=True), nullable=False)
    session_id = Column(UUID(as_uuid=True), nullable=False)

    # The accepted specification, frozen at creation.
    accepted_by_change_id = Column(UUID(as_uuid=True), nullable=False)
    # {v, max_attempts, timeout_s, idempotent, fresh, privacy_mode, resumable?,
    #  gate_timeout_s?, seed_from?}: a deploy or settings change cannot alter it.
    execution_policy = Column(JSONB, nullable=False)
    target_version = Column(Text, nullable=False)

    # Frozen at first start: the agent's compiled prompt and the port-level pins
    # {v, ports: {port: {node_id, run_id, port, type, ref}}, target_version}.
    compiled_prompt = Column(Text, nullable=True)
    resolved_inputs = Column(JSONB(none_as_null=True), nullable=True)

    status = Column(Text, nullable=False)
    # Set when an applied change supersedes it: no longer current, never selectable again; kept as history.
    superseded_at = Column(DateTime(timezone=True), nullable=True)
    # {v, ports: {port: {type, ref: {table, ids}, summary, phi}}}, written with
    # `completed` when the run succeeds, and immutable afterwards.
    outputs = Column(JSONB(none_as_null=True), nullable=True)
    # `expectation_violated`, set once at completion; the next live node waits until a
    # change releases it (hold_released_by_change_id) or reruns this node.
    downstream_hold = Column(Text, nullable=True)
    hold_released_by_change_id = Column(UUID(as_uuid=True), nullable=True)

    # The agent's scratchpad: labels, not authority (`outputs` is).
    scratchpad_notes = Column(JSONB(none_as_null=True), nullable=True)
    scratchpad_warnings = Column(JSONB(none_as_null=True), nullable=True)
    scratchpad_summary = Column(JSONB(none_as_null=True), nullable=True)
    # A copy of the latest attempt's error, written in the same transaction.
    error = Column(JSONB(none_as_null=True), nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    node = relationship("GraphNode", back_populates="runs", foreign_keys=[node_id])
    attempts = relationship(
        "RunAttempt",
        back_populates="run",
        order_by="RunAttempt.attempt_number",
        lazy="selectin",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["node_id", "session_id"],
            ["graph_nodes.node_id", "graph_nodes.session_id"],
            name="fk_node_runs_node_id_session_id_graph_nodes",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["accepted_by_change_id", "session_id"],
            ["graph_changes.change_id", "graph_changes.session_id"],
            name="fk_node_runs_accepted_by_change_id_graph_changes",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["hold_released_by_change_id", "session_id"],
            ["graph_changes.change_id", "graph_changes.session_id"],
            name="fk_node_runs_hold_released_by_change_id_graph_changes",
            ondelete="RESTRICT",
        ),
        # Supporting tuple: domain rows and attempts prove same-session
        # alignment by composite FK onto this.
        UniqueConstraint("run_id", "session_id", name="uq_node_runs_run_id"),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'cancelled')", name="status"
        ),
        CheckConstraint(
            "(status IN ('completed', 'failed', 'cancelled')) = (completed_at IS NOT NULL)",
            name="completed_at_iff_terminal",
        ),
        CheckConstraint(
            "started_at IS NOT NULL OR (status = 'cancelled' AND execution_policy ? 'legacy')",
            name="started_unless_never_started",
        ),
        CheckConstraint(
            "started_at IS NULL OR resolved_inputs IS NOT NULL", name="inputs_before_start"
        ),
        CheckConstraint("status <> 'failed' OR error IS NOT NULL", name="failed_has_error"),
        CheckConstraint(
            "downstream_hold IS NULL OR (status = 'completed' "
            "AND downstream_hold IN ('expectation_violated', 'user_review'))",
            name="downstream_hold",
        ),
        CheckConstraint(
            "hold_released_by_change_id IS NULL OR downstream_hold IS NOT NULL",
            name="hold_release_needs_a_hold",
        ),
        CheckConstraint(
            "outputs IS NULL OR (status = 'completed' AND jsonb_typeof(outputs) = 'object' "
            "AND outputs ? 'v')",
            name="outputs_only_when_completed",
        ),
        CheckConstraint(
            "jsonb_typeof(execution_policy) = 'object' AND execution_policy ? 'v' "
            "AND (resolved_inputs IS NULL OR resolved_inputs ? 'v' OR execution_policy ? 'legacy')",
            name="versioned",
        ),
        CheckConstraint("length(btrim(target_version)) > 0", name="target_version_not_blank"),
        CheckConstraint(
            "compiled_prompt IS NULL OR octet_length(compiled_prompt) <= "
            f"{COMPILED_PROMPT_MAX_BYTES}",
            name="compiled_prompt_bounded",
        ),
        CheckConstraint(
            " AND ".join(
                f"coalesce(pg_column_size({column}), 0) <= {NODE_RUN_JSON_MAX_BYTES}"
                for column in (
                    "resolved_inputs",
                    "scratchpad_notes",
                    "scratchpad_warnings",
                    "scratchpad_summary",
                    "error",
                )
            ),
            name="json_bounded",
        ),
        CheckConstraint(
            "pg_column_size(execution_policy) <= 65536 "
            "AND COALESCE(pg_column_size(outputs), 0) <= 2097152",
            name="policy_and_outputs_bounded",
        ),
        # At most one current run per node.
        Index(
            "uq_node_runs_one_current",
            "node_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        Index(
            "ix_node_runs_session_id_current",
            "session_id",
            postgresql_where=text("superseded_at IS NULL"),
        ),
        Index("ix_node_runs_node_id_started_at", "node_id", "started_at"),
        Index("ix_node_runs_session_id_status", "session_id", "status"),
    )
