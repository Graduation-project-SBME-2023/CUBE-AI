from sqlalchemy import Column, String, Boolean, Text, DateTime, text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from application_api.db.base import Base

class TaskMessage(Base):
    """
    Structured task payload per execution agent.
    agent_id == the PlanStep.id that owns this message.
    """
    __tablename__ = "task_messages"
 
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()")
    )
 
    # The owning PlanStep — agent_id IS the step id
    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("plan_steps.id", ondelete="CASCADE"),
        nullable=False,
        unique=True                             # one TaskMessage per PlanStep
    )
 
    task_id            = Column(String(255), nullable=False, unique=True)
    phase              = Column(String(100), nullable=False, server_default="")
    agent_title        = Column(String(255), nullable=False, server_default="")
    instructions       = Column(Text,        nullable=False, server_default="")
    hypothesis_summary = Column(Text,        nullable=False, server_default="")
    payload            = Column(JSONB,       nullable=True,  server_default="{}")
 
    context_augmentation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_augmentations.id", ondelete="SET NULL"),
        nullable=True
    )
 
    created_at = Column(DateTime(timezone=True), server_default=func.now())
 
    # single_parent=True + delete-orphan: a ContextAugmentation belongs to
    # exactly one TaskMessage, so deleting or reassigning the TaskMessage
    # must delete the ContextAugmentation too. Without this, the FK's
    # ondelete="SET NULL" (on context_augmentation_id) only protects against
    # dangling references — it does not clean up the ContextAugmentation row
    # itself, leaving it permanently orphaned.
    context_augmentation = relationship(
        "ContextAugmentation",
        back_populates="task_message",
        cascade="all, delete-orphan",
        single_parent=True,
        # ORM-level delete-orphan cascade only fires for loaded relationships;
        # eager-load it so `db.delete(task_message)` reliably cascades
        # regardless of the query path that fetched the row.
        lazy="selectin",
    )
    plan_step            = relationship("PlanStep", back_populates="task_message")
 