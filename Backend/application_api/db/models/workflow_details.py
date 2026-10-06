from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
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
    task_name = Column("task_name", Text, ForeignKey("workflow.task_name", ondelete="RESTRICT"), primary_key=True)
    study_uid = Column("study_uid", Text, primary_key=True)
    volume_series_instance_uid = Column("volume_series_instance_uid", Text, primary_key=True)
    label_series_instance_uid = Column(
        "label_series_instance_uid",
        Text,
        ForeignKey("segmentation_masks.label_series_instance_uid", ondelete="RESTRICT"),
        nullable=True,
    )
    annotated = Column("annotated", Boolean, server_default="false")
    train = Column("train", Boolean, server_default="true")
    modality = Column("modality", Text)

    #training input must be *all* frozen members in deterministic order.
    # Multiple channels share a case_index and differ by channel_index; the
    # nnUNet dataset.json and instance_mapping are regenerated from these rows
    # on demand rather than read back from a file.
    case_index = Column("case_index", Integer, nullable=False)
    channel_index = Column("channel_index", Integer, nullable=False, server_default="0")
    # The stable patient-level split group (review proposed removing this;
    # REJECTED — it is what keeps a patient out of both sides of a split).
    subject_key = Column("subject_key", Text, nullable=True)

    __table_args__ = (
        # A member's volume must actually belong to the study it claims.
        ForeignKeyConstraint(
            ["study_uid", "volume_series_instance_uid"],
            ["volume_series.study_uid", "volume_series.volume_series_instance_uid"],
            name="fk_workflow_details_study_uid_volume_series",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "task_name", "case_index", "channel_index", name="uq_workflow_details_task_name"
        ),
        CheckConstraint("case_index > 0", name="case_index_positive"),
        CheckConstraint("channel_index >= 0", name="channel_index_nonnegative"),
        Index("ix_workflow_details_task_study", "task_name", "study_uid"),
    )
