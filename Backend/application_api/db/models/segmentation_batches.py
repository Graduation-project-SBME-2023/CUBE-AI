from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from application_api.db.base import Base
from application_api.db.models.cohorts import _versioned

REVIEW_STATUSES = ("not_ready", "pending", "submitting", "submitted")
PAIR_STATUSES = ("pending", "running", "ready", "failed")
REVIEW_DECISIONS = ("accepted", "modified", "rejected")


class SegmentationBatch(Base):
    """One segmentation batch over a cohort."""

    __tablename__ = "segmentation_batches"

    segmentation_batch_id = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_by_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("node_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    cohort_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cohorts.cohort_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    batch_key = Column(Text, nullable=False)
    config = Column(JSONB, nullable=False)
    review_status = Column(Text, nullable=False, server_default="not_ready")
    review_request_hash = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Targets and pairs are read with explicit queries in the repository: the
    # pairs reach this table only through composite FKs, and an implicit
    # relationship would have to pick one of them.

    __table_args__ = (
        UniqueConstraint(
            "created_by_run_id",
            "batch_key",
            name="uq_segmentation_batches_created_by_run_id",
        ),
        # Restored supporting tuple the pairs' composite FK targets.
        UniqueConstraint(
            "segmentation_batch_id",
            "cohort_id",
            name="uq_segmentation_batches_segmentation_batch_id",
        ),
        CheckConstraint("length(btrim(batch_key)) > 0", name="batch_key_not_blank"),
        CheckConstraint(_versioned("config", nullable=False), name="config_versioned"),
        CheckConstraint(
            "review_status IN ('not_ready', 'pending', 'submitting', 'submitted')",
            name="review_status",
        ),
        CheckConstraint(
            "(review_status IN ('submitting', 'submitted')) = (review_request_hash IS NOT NULL)",
            name="hash_iff_submitting",
        ),
        CheckConstraint(
            "(review_status = 'submitted') = (reviewed_at IS NOT NULL)",
            name="reviewed_at_iff_submitted",
        ),
        CheckConstraint(
            "review_request_hash IS NULL OR review_request_hash ~ '^[0-9a-f]{64}$'",
            name="review_request_hash_is_a_digest",
        ),
    )


class SegmentationTarget(Base):
    """One clinical target of a batch."""

    __tablename__ = "segmentation_targets"

    target_id = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    segmentation_batch_id = Column(
        UUID(as_uuid=True),
        ForeignKey("segmentation_batches.segmentation_batch_id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_key = Column(Text, nullable=False)
    label = Column(Text, nullable=False)
    specification = Column(JSONB, nullable=False)
    inference_model_id = Column(
        UUID(as_uuid=True),
        ForeignKey("inference_models.inference_model_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "segmentation_batch_id",
            "target_key",
            name="uq_segmentation_targets_segmentation_batch_id",
        ),
        UniqueConstraint(
            "segmentation_batch_id",
            "target_id",
            name="uq_segmentation_targets_segmentation_batch_id_target_id",
        ),
        CheckConstraint("length(btrim(target_key)) > 0", name="target_key_not_blank"),
        CheckConstraint("length(btrim(label)) > 0", name="label_not_blank"),
        CheckConstraint(
            _versioned("specification", nullable=False), name="specification_versioned"
        ),
    )


class SegmentationPair(Base):
    """One (target, volume) result and its review."""

    __tablename__ = "segmentation_pairs"

    pair_id = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    segmentation_batch_id = Column(UUID(as_uuid=True), nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=False)
    volume_series_instance_uid = Column(Text, nullable=False)
    cohort_id = Column(UUID(as_uuid=True), nullable=False)
    cohort_member_id = Column(UUID(as_uuid=True), nullable=False)
    inference_job_id = Column(Text, nullable=True)
    status = Column(Text, nullable=False, server_default="pending")
    candidate_segmentation_uid = Column(Text, nullable=True, index=True)
    selected_segmentation_uid = Column(Text, nullable=True, index=True)
    segment_number = Column(Integer, nullable=False, server_default="1")
    review_decision = Column(Text, nullable=True)
    # Recovery/resume checkpoint state for the crashed-batch resume path
    # (`batch_store.py`), never a QA/Dice score — renamed from `quality`,
    # which was misleading (see execution_checkpoint_versioned below).
    execution_checkpoint = Column(JSONB(none_as_null=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "target_id", "volume_series_instance_uid", name="uq_segmentation_pairs_target_id"
        ),
        ForeignKeyConstraint(
            ["segmentation_batch_id", "target_id"],
            ["segmentation_targets.segmentation_batch_id", "segmentation_targets.target_id"],
            name="fk_segmentation_pairs_segmentation_batch_id_target_id",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["segmentation_batch_id", "cohort_id"],
            ["segmentation_batches.segmentation_batch_id", "segmentation_batches.cohort_id"],
            name="fk_segmentation_pairs_segmentation_batch_id_cohort_id",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["cohort_id", "cohort_member_id", "volume_series_instance_uid"],
            [
                "cohort_members.cohort_id",
                "cohort_members.member_id",
                "cohort_members.volume_series_instance_uid",
            ],
            name="fk_segmentation_pairs_cohort_member",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["candidate_segmentation_uid", "volume_series_instance_uid"],
            [
                "segmentation_masks.label_series_instance_uid",
                "segmentation_masks.volume_series_instance_uid",
            ],
            name="fk_segmentation_pairs_candidate_segmentation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["selected_segmentation_uid", "volume_series_instance_uid"],
            [
                "segmentation_masks.label_series_instance_uid",
                "segmentation_masks.volume_series_instance_uid",
            ],
            name="fk_segmentation_pairs_selected_segmentation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'ready', 'failed')", name="status"
        ),
        CheckConstraint("segment_number > 0", name="segment_number_positive"),
        CheckConstraint(
            "review_decision IS NULL OR review_decision IN ('accepted', 'modified', 'rejected')",
            name="review_decision",
        ),
        CheckConstraint(
            "selected_segmentation_uid IS NULL OR review_decision IN ('accepted', 'modified')",
            name="selected_implies_positive_decision",
        ),
        CheckConstraint(
            "inference_job_id IS NULL OR length(btrim(inference_job_id)) > 0",
            name="inference_job_id_not_blank",
        ),
        CheckConstraint(
            _versioned("execution_checkpoint", nullable=True), name="execution_checkpoint_versioned"
        ),
        Index(
            "ix_segmentation_pairs_segmentation_batch_id_status", "segmentation_batch_id", "status"
        ),
        Index(
            "ix_segmentation_pairs_cohort_id_cohort_member_id", "cohort_id", "cohort_member_id"
        ),
    )
