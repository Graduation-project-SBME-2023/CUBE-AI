
from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, text,ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from application_api.db.base import Base  
from sqlalchemy.dialects.postgresql import UUID



class Hypothesis(Base):
    __tablename__ = "hypotheses"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text('gen_random_uuid()')
    )

    chat_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.chat_id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    title = Column(String(255), nullable=False)
    population = Column(Text)
    intervention = Column(Text)
    hypothesis = Column(Text)
    comparison = Column(Text)
    outcome = Column(Text)
    clinical_context = Column(Text)
    relationship_type = Column(String(50))
    state = Column(String(50), default="not_confirmed")

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    plan = relationship("Plan", back_populates="hypothesis", uselist=False, cascade="all, delete-orphan")
    chat_session = relationship("ChatSession", back_populates="hypothesis")
