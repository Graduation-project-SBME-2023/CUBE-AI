from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from application_api.db.base import Base

TRAINING_OUTCOMES = ("completed", "failed", "cancelled")


class TrainingJob(Base):
    """The durable receipt of one standalone nnUNet training, committed
    before the job is submitted, so a lost response never trains twice."""

    __tablename__ = "training_jobs"

    # Minted and committed before the POST; MONAI's dedupe key.
    job_id = Column(Text, primary_key=True)
    task_name = Column(
        Text, ForeignKey("workflow.task_name", ondelete="RESTRICT"), nullable=False
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # NULL: the submission is uncertain until MONAI acknowledges it.
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
    # The confirmed terminal outcome.
    ended_at = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(Text, nullable=True)
    error = Column(JSONB(none_as_null=True), nullable=True)

    __table_args__ = (
        CheckConstraint("length(btrim(job_id)) > 0", name="job_id_not_blank"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('completed', 'failed', 'cancelled')", name="outcome"
        ),
        CheckConstraint("(ended_at IS NULL) = (outcome IS NULL)", name="ended_at_iff_outcome"),
        Index(
            "uq_training_jobs_one_active",
            "task_name",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
    )
