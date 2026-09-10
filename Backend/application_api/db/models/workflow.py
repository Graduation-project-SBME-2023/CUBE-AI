from sqlalchemy import Column, String, Text, DateTime
from sqlalchemy.sql import func
from application_api.db.base import Base

class Workflow(Base):
    __tablename__ = 'workflow'

    # Task-name registry that workflow_details FKs into and that the nnUNet
    # training routes read. Previously a single-column stub; the columns below
    # were added so a task carries its own creation time and lifecycle state
    # instead of being an opaque bare string.
    task_name = Column(String, primary_key=True, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, server_default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
