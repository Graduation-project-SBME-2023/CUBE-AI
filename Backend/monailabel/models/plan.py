


from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, text,ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from core.database import Base  
from sqlalchemy.dialects.postgresql import UUID

class Plan(Base):
    __tablename__ = "plans"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text('gen_random_uuid()')
    )

    hypothesis_id = Column(
        UUID(as_uuid=True),
        ForeignKey("hypotheses.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    study_goal = Column(String(255), nullable=False)
    state = Column(String(50), default="not_confirmed")
    rationale=Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    hypothesis = relationship("Hypothesis", back_populates="plan")
    steps = relationship("PlanStep", back_populates="plan", cascade="all, delete-orphan")
