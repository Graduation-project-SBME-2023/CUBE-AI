from sqlalchemy import Column, Text, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from core.database import Base

class WorkflowDetail(Base):
    __tablename__ = "workflow_details"

    task_name = Column(Text, ForeignKey("workflow.task_name", ondelete="CASCADE"), primary_key=True)
    study_UID = Column(Text, primary_key=True)
    volume_series_instance_UID = Column(Text, primary_key=True)
    label_series_instance_UID = Column(Text, nullable=True)
    annotated = Column(Boolean, server_default="false")
    train = Column(Boolean, server_default="true")
    modality = Column(Text)

    # optional relationship (if Workflow model exists)
    # workflow = relationship("Workflow", back_populates="details", lazy="joined")