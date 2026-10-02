from sqlalchemy import Column, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID

from application_api.db.base import Base


class SessionGraph(Base):
    """The one session graph a session owns."""

    __tablename__ = "session_graphs"

    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="RESTRICT"),
        primary_key=True,
    )

    # NULL = unversioned, editable draft. Setting it is one-way and is
    # performed only by the approval transaction, which
    # also inserts one pending run per node. Non-NULL means the graph and
    # every node are immutable.
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
    nodes = relationship(
        "GraphNode",
        back_populates="session_graph",
        order_by="GraphNode.position",
        lazy="selectin",
    )
