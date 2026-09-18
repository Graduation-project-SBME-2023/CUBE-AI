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


class PlanStep(Base):
    """One step of a session's plan."""

    __tablename__ = "plan_steps"

    step_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("plans.session_id", ondelete="RESTRICT"),
        nullable=False,
    )

    step_order = Column(Integer, nullable=False)
    agent_type = Column(String(255), nullable=False)
    title = Column(String(255), nullable=False)
    instructions = Column(JSONB, nullable=False, server_default="{}")

    # no is_enabled, no timestamps. /: no status and no logs —
    # execution state lives on agent_runs, and a second copy here could
    # disagree with it.

    plan = relationship("Plan", back_populates="steps")
    # 0 while the plan is a draft, exactly 1 once it is approved. The
    # compiled task message the `task_messages` table used to hold is part of
    # the this flowEFINITION and lives in `instructions["task_message"]` now
    agent_run = relationship(
        "AgentRun",
        back_populates="step",
        foreign_keys="AgentRun.step_id",
        uselist=False,
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint("step_order > 0", name="step_order_positive"),
        # DEFERRABLE so a reorder can swap two step_orders inside one
        # transaction without an intermediate collision.
        UniqueConstraint(
            "session_id",
            "step_order",
            name="uq_plan_steps_session_id",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        # Free (step_id is the PK) and required as the target of agent_runs'
        # composite (step_id, session_id) FK in this flow.
        UniqueConstraint("step_id", "session_id", name="uq_plan_steps_step_id"),
    )
