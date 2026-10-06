from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.sql import func

from application_api.db.base import Base

CHANGE_AUTHORS = ("main", "user", "harness", "legacy")
CHANGE_STATUSES = ("pending", "applied", "rejected", "stale", "withdrawn", "expired")
# The two approval reasons the Application API derives itself, whatever the
# caller sent.
PLAN_MODE = "PLAN_MODE"
SUPERSEDES_COMPLETED = "supersedes_completed"


class GraphChange(Base):
    """One immutable, submitted mutation of a session graph, with its approval.
    The only durable approval mechanism and the only history of accepted
    node definitions: a run finds its definition here by `node_id`."""

    __tablename__ = "graph_changes"

    change_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("session_graphs.session_id", ondelete="RESTRICT"),
        nullable=False,
    )
    author = Column(Text, nullable=False)
    idempotency_key = Column(Text, nullable=False)
    base_version = Column(Integer, nullable=False)
    # The full target sequence (v1): every live node with its complete
    # definition, rerun_node_ids, release_hold_node_ids, on_running, origin?
    content = Column(JSONB, nullable=False)
    # SHA-256 of the RFC 8785 canonical JSON of `content`, computed here.
    content_digest = Column(Text, nullable=False)
    approval_reasons = Column(ARRAY(Text), nullable=False, server_default="{}")
    status = Column(Text, nullable=False)
    decided_by = Column(Text, nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    applied_version = Column(Integer, nullable=True)
    # The approval TTL (APPROVAL_TTL_H) is measured from it.
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("session_id", "idempotency_key", name="uq_graph_changes_session_id_idempotency_key"),
        UniqueConstraint("session_id", "applied_version", name="uq_graph_changes_session_id_applied_version"),
        # The composite FK target of graph_nodes and node_runs.
        UniqueConstraint("change_id", "session_id", name="uq_graph_changes_change_id_session_id"),
        CheckConstraint("author IN ('main', 'user', 'harness', 'legacy')", name="author"),
        CheckConstraint(
            "status IN ('pending', 'applied', 'rejected', 'stale', 'withdrawn', 'expired')",
            name="status",
        ),
        CheckConstraint("decided_by IS NULL OR decided_by IN ('user', 'auto')", name="decided_by"),
        CheckConstraint("(status = 'applied') = (applied_version IS NOT NULL)", name="applied_iff_version"),
        CheckConstraint(
            "applied_version IS NULL OR applied_version = base_version + 1",
            name="applied_version_follows_base",
        ),
        CheckConstraint(
            "(status IN ('applied', 'rejected')) = (decided_at IS NOT NULL AND decided_by IS NOT NULL)",
            name="decided_iff_decision",
        ),
        CheckConstraint(
            "cardinality(approval_reasons) = 0 OR decided_by IS DISTINCT FROM 'auto'",
            name="reasons_need_a_user",
        ),
        CheckConstraint("content_digest ~ '^[0-9a-f]{64}$'", name="content_digest_is_sha256"),
        CheckConstraint(
            "jsonb_typeof(content) = 'object' AND content ? 'v' AND pg_column_size(content) <= 2097152",
            name="content_versioned",
        ),
        CheckConstraint("base_version >= 0", name="base_version_nonnegative"),
        CheckConstraint("length(btrim(idempotency_key)) > 0", name="idempotency_key_not_blank"),
        Index(
            "uq_graph_changes_one_pending",
            "session_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_graph_changes_session_id_created_at", "session_id", "created_at"),
    )
