from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from application_api.db.base import Base


class SessionGraph(Base):
    """The one live graph of a session: its header and CAS row."""

    __tablename__ = "session_graphs"

    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="RESTRICT"),
        primary_key=True,
    )

    # The compare-and-swap token. Only applying a change advances it, by exactly one, and only
    # the transaction that advances it may change the graph's nodes.
    version = Column(Integer, nullable=False, server_default="0")

    # Legacy: derived from change #1 (`decided_at` of applied_version 1). No new
    # writers; dropped with AutoGen.
    approved_at = Column(DateTime(timezone=True), nullable=True)

    # No created_at: the graph is created in the same transaction as its
    # session, so sessions.created_at already records that instant.
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    session = relationship("Session", back_populates="session_graph")
    # Every node, including removed ones; never loaded implicitly (the
    # repositories read the live sequence they need).
    nodes = relationship(
        "GraphNode",
        back_populates="session_graph",
        order_by="GraphNode.position",
        lazy="raise",
    )

    __table_args__ = (CheckConstraint("version >= 0", name="version_nonnegative"),)
