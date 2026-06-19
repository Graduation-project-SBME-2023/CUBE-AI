from sqlalchemy import Column, String, Boolean, Text, DateTime, text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from core.database import Base

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
 
    context_augmentation = relationship("ContextAugmentation", back_populates="task_message")
    plan_step            = relationship("PlanStep", back_populates="task_message")
 