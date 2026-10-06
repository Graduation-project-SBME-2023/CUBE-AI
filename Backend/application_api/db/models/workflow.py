from sqlalchemy import CheckConstraint, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from application_api.db.base import Base

class Workflow(Base):
    __tablename__ = 'workflow'

    # Task-name registry that workflow_details FKs into and that the nnUNet
    # training routes read.
    task_name = Column(Text, primary_key=True, nullable=False)

    #  the numeric nnUNet dataset id is allocated under an application-api
    # lock and this UNIQUE constraint — never from the max local directory,
    # which cannot see a concurrent allocation.
    dataset_id = Column(Integer, nullable=False, unique=True)

    # the training-setup UI reads it directly,
    # and it is distinct from the task_name code key.
    display_title = Column(Text, nullable=True)

    description = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, server_default="active")

    # Channel names, label map, preprocessing/training settings. Frozen
    # together with membership while any accepted training job references it.
    definition = Column(JSONB, nullable=False, server_default="{}")
    frozen_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint("dataset_id > 0", name="dataset_id_positive"),
    )
