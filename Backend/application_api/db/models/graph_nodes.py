from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, ExcludeConstraint
from sqlalchemy.orm import relationship

from application_api.db.base import Base

TARGET_KINDS = ("agent", "tool")


class GraphNode(Base):
    """A stable unit of work in the graph. Its definition changes only
    through an applied graph change; its `node_id` never changes."""

    __tablename__ = "graph_nodes"

    node_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("session_graphs.session_id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Short per-session handle (`n3`), assigned when the change is applied and never reused.
    node_seq = Column(Integer, nullable=False)
    # The execution order of live nodes.
    position = Column(Integer, nullable=False)

    # The target: an agent or a tool. Kind and name are immutable per node.
    target_kind = Column(Text, nullable=False, server_default="agent")
    target_name = Column(String(255), nullable=False)
    target_version = Column(Text, nullable=False, server_default="legacy")

    # The current definition, changed only by an applied change.
    title = Column(String(255), nullable=False)
    params = Column(JSONB, nullable=False, server_default="{}")
    # {v, ports: {port: {node_id, port}}}: the source of an ambiguous input.
    input_sources = Column(JSONB(none_as_null=True), nullable=True)
    # {v, rules: [{metric, op, value, port?}]}: checked when the run completes.
    expectations = Column(JSONB(none_as_null=True), nullable=True)
    # D28, part of the definition: `before` waits for the user's release,
    # `after` holds the next node for the user's review.
    approval = Column(Text, nullable=False, server_default="none")
    # The user's change that released an `approval: before` node; cleared
    # whenever the node is edited or rerun.
    start_released_by_change_id = Column(UUID(as_uuid=True), nullable=True)

    # The applied change that last added, edited or reran this node.
    accepted_by_change_id = Column(UUID(as_uuid=True), nullable=False)
    # One-way tombstone: a node is never deleted.
    removed_at = Column(DateTime(timezone=True), nullable=True)

    session_graph = relationship("SessionGraph", back_populates="nodes")
    # Every run, oldest first; at most one is current (superseded_at IS NULL).
    # Never loaded implicitly: the repositories query the run they need.
    runs = relationship(
        "NodeRun",
        back_populates="node",
        foreign_keys="NodeRun.node_id",
        order_by="NodeRun.started_at",
        lazy="raise",
    )

    __table_args__ = (
        CheckConstraint("position > 0", name="position_positive"),
        CheckConstraint("node_seq > 0", name="node_seq_positive"),
        CheckConstraint("target_kind IN ('agent', 'tool')", name="target_kind"),
        CheckConstraint("length(btrim(target_version)) > 0", name="target_version_not_blank"),
        CheckConstraint("approval IN ('none', 'before', 'after')", name="approval"),
        CheckConstraint(
            "start_released_by_change_id IS NULL OR approval = 'before'", name="start_release_needs_before"
        ),
        CheckConstraint(
            "(input_sources IS NULL OR (jsonb_typeof(input_sources) = 'object' AND input_sources ? 'v')) "
            "AND (expectations IS NULL OR (jsonb_typeof(expectations) = 'object' AND expectations ? 'v'))",
            name="definition_versioned",
        ),
        # DEFERRABLE so applying a change can reorder live nodes inside one transaction.
        ExcludeConstraint(
            ("session_id", "="),
            ("position", "="),
            name="ex_graph_nodes_live_position",
            using="btree",
            where=text("removed_at IS NULL"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        UniqueConstraint("session_id", "node_seq", name="uq_graph_nodes_session_id_node_seq"),
        # The target of node_runs' composite (node_id, session_id) FK.
        UniqueConstraint("node_id", "session_id", name="uq_graph_nodes_node_id"),
        ForeignKeyConstraint(
            ["accepted_by_change_id", "session_id"],
            ["graph_changes.change_id", "graph_changes.session_id"],
            name="fk_graph_nodes_accepted_by_change_id_graph_changes",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["start_released_by_change_id", "session_id"],
            ["graph_changes.change_id", "graph_changes.session_id"],
            name="fk_graph_nodes_start_released_by_change_id_graph_changes",
            ondelete="RESTRICT",
        ),
        Index("ix_graph_nodes_accepted_by_change_id", "accepted_by_change_id"),
        Index("ix_graph_nodes_session_id_position", "session_id", "position"),
    )
