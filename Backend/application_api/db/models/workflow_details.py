from sqlalchemy import Column, Text, Boolean, ForeignKey
from application_api.db.base import Base


class WorkflowDetail(Base):
    __tablename__ = "workflow_details"

    # Explicit lowercase column names: the attribute names below (study_UID,
    # etc.) are mixed-case, and without an explicit name string SQLAlchemy
    # would quote and target a literal "study_UID" column — which does not
    # exist, since the actual DB column (created via unquoted raw SQL) is
    # lowercase study_uid. This mismatch made this ORM class unusable; the
    # workflow.py routes worked around it entirely by bypassing the ORM and
    # running raw asyncpg SQL directly against the pool connection instead
    # (see database_audit.md D-20/D-21).
    task_name = Column("task_name", Text, ForeignKey("workflow.task_name", ondelete="CASCADE"), primary_key=True)
    study_uid = Column("study_uid", Text, primary_key=True)
    volume_series_instance_uid = Column("volume_series_instance_uid", Text, primary_key=True)
    label_series_instance_uid = Column("label_series_instance_uid", Text, nullable=True)
    annotated = Column("annotated", Boolean, server_default="false")
    train = Column("train", Boolean, server_default="true")
    modality = Column("modality", Text)
