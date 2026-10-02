from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from application_api.db.base import Base


class GraphNode(Base):
    """One node of a session's graph."""

    __tablename__ = "graph_nodes"

    node_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("session_graphs.session_id", ondelete="RESTRICT"),
        nullable=False,
    )

    position = Column(Integer, nullable=False)
    target_name = Column(String(255), nullable=False)
    title = Column(String(255), nullable=False)
    params = Column(JSONB, nullable=False, server_default="{}")

    # no is_enabled, no timestamps. /: no status and no logs —
    # execution state lives on node_runs, and a second copy here could
    # disagree with it.

    session_graph = relationship("SessionGraph", back_populates="nodes")
    # 0 while the graph is a draft, exactly 1 once it is approved. The
    # compiled task message the `task_messages` table used to hold is part of
    # the this flowEFINITION and lives in `params["task_message"]` now
    run = relationship(
        "NodeRun",
        back_populates="node",
        foreign_keys="NodeRun.node_id",
        uselist=False,
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint("position > 0", name="position_positive"),
        # DEFERRABLE so a reorder can swap two positions inside one
        # transaction without an intermediate collision.
        UniqueConstraint(
            "session_id",
            "position",
            name="uq_graph_nodes_session_id",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        # Free (node_id is the PK) and required as the target of node_runs'
        # composite (node_id, session_id) FK in this flow.
        UniqueConstraint("node_id", "session_id", name="uq_graph_nodes_node_id"),
    )
