from sqlalchemy import Column, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID

from application_api.db.base import Base


class Plan(Base):
    """The one plan a session owns."""

    __tablename__ = "plans"

    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="RESTRICT"),
        primary_key=True,
    )

    # NULL = unversioned, editable draft. Setting it is one-way and is
    # performed only by the approval transaction, which
    # also inserts one pending agent_run per step. Non-NULL means the plan and
    # every step are immutable.
    approved_at = Column(DateTime(timezone=True), nullable=True)

    # No created_at: the plan is created in the same transaction as its
    # session, so sessions.created_at already records that instant.
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    session = relationship("Session", back_populates="plan")
    steps = relationship(
        "PlanStep",
        back_populates="plan",
        order_by="PlanStep.step_order",
        lazy="selectin",
    )
