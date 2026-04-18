from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, text,ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from unittest.mock import Base


from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from core.database import Base  
from sqlalchemy.dialects.postgresql import UUID

class ContextAugmentation(Base):
    """
    Domain knowledge injected into each agent's task message.
    """
    __tablename__ = "context_augmentations"
 
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()")
    )
 
    knowledge_summary    = Column(Text, nullable=False, server_default="")  # Agent-relevant slice of the knowledge base
    literature_findings  = Column(Text, nullable=False, server_default="")  # Key findings from reviews / papers
    methodology_guidance = Column(Text, nullable=False, server_default="")  # Relevant methods, best practices
    terminology          = Column(Text, nullable=False, server_default="")  # MeSH terms, controlled vocabulary
    sources_guide        = Column(Text, nullable=False, server_default="")  # Where to find more information
 
    created_at = Column(DateTime(timezone=True), server_default=func.now())
 
    task_message = relationship("TaskMessage", back_populates="context_augmentation", uselist=False)