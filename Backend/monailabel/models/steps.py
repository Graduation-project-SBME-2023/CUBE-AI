

from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, text,ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from core.database import Base  
from sqlalchemy.dialects.postgresql import UUID

class PlanStep(Base):
    __tablename__ = "plan_steps"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text('gen_random_uuid()')
    )

    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="CASCADE"),
        nullable=False
    )

    title = Column(String(255), nullable=False)
    description = Column(Text)
    module = Column(String(255))
    status = Column(String(50), default="pending")

    order = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    logs = Column(JSONB, nullable=True, server_default='[]')

    plan = relationship("Plan", back_populates="steps")
