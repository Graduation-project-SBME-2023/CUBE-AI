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
    COMPILED_PROMPT_MAX_BYTES,
    NODE_RUN_JSON_MAX_BYTES,
)
from application_api.db.base import Base

# current. One execution of the target a graph node selects.
NODE_RUN_STATUSES = ("pending", "running", "completed", "failed")


class NodeRun(Base):
    """Execution state only — never a copy of the node definition."""

    __tablename__ = "node_runs"

    run_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # No standalone FK here: the composite (node_id, session_id) FK below
    # already proves existence and same-session ownership together, so a
    # second single-column FK on node_id would only duplicate it.
    node_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
    )
    session_id = Column(UUID(as_uuid=True), nullable=False)

    # The compiler's exact output, assembled from the this flowLUS scratchpad
    # state and upstream results — not reconstructible, and therefore not a
    # copy of anything. Resolved immediately before the node starts and
    # immutable after that transition.
    compiled_prompt = Column(Text, nullable=True)
    resolved_inputs = Column(JSONB(none_as_null=True), nullable=True)

    status = Column(String, nullable=False, server_default="pending")

    # file-elimination map: state/scratchpad.json and
    # outputs/agents/{agent_id}_output.json land here.
    scratchpad_notes = Column(JSONB(none_as_null=True), nullable=True)
    scratchpad_warnings = Column(JSONB(none_as_null=True), nullable=True)
    scratchpad_summary = Column(JSONB(none_as_null=True), nullable=True)
    error = Column(JSONB(none_as_null=True), nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # The only FK path to graph_nodes is the composite (node_id, session_id)
    # alignment FK below; the relationship still joins on node_id alone.
    node = relationship("GraphNode", back_populates="run", foreign_keys=[node_id])

    __table_args__ = (
        ForeignKeyConstraint(
            ["node_id", "session_id"],
            ["graph_nodes.node_id", "graph_nodes.session_id"],
            name="fk_node_runs_node_id_session_id_graph_nodes",
            ondelete="RESTRICT",
        ),
        # Supporting tuple: domain rows prove same-session alignment by
        # composite FK onto this.
        UniqueConstraint("run_id", "session_id", name="uq_node_runs_run_id"),
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
            "AND length(btrim(compiled_prompt)) > 0 AND resolved_inputs IS NOT NULL)",
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
        Index("ix_node_runs_session_id_status", "session_id", "status"),
    )
