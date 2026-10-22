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
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from application_api.core.json_bounds import CLINICAL_DATA_MAX_BYTES, DOMAIN_JSON_MAX_BYTES
from application_api.db.base import Base

# Manual override is a SOURCE, not a confidence value.
LABEL_SOURCES = ("pending", "llm_extraction", "manual_override", "uploaded_targets")
LABEL_CONFIDENCES = ("high", "medium", "low", "unlabeled")


def _versioned(column: str, *, nullable: bool) -> str:
    rule = (
        f"(jsonb_typeof({column}) = 'object' "
        f"AND jsonb_typeof({column}->'schema_version') = 'number' "
        f"AND pg_column_size({column}) <= {DOMAIN_JSON_MAX_BYTES})"
    )
    return f"({column} IS NULL OR {rule})" if nullable else rule


class Cohort(Base):
    """The study population one run defined."""

    __tablename__ = "cohorts"

    cohort_id = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_by_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("node_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    cohort_key = Column(Text, nullable=False)
    criteria = Column(JSONB, nullable=False)
    label_definition = Column(JSONB, nullable=False)
    selection_summary = Column(JSONB(none_as_null=True), nullable=True)
    frozen_at = Column(DateTime(timezone=True), nullable=True)
    # Who froze it (D28): `user` (the doctor's approval, asked for) or `auto`
    # (the population node itself, when no approval was asked for).
    frozen_by = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    members = relationship(
        "CohortMember",
        back_populates="cohort",
        order_by="CohortMember.ordinal",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint(
            "created_by_run_id", "cohort_key", name="uq_cohorts_created_by_run_id"
        ),
        CheckConstraint("length(btrim(cohort_key)) > 0", name="cohort_key_not_blank"),
        CheckConstraint(_versioned("criteria", nullable=False), name="criteria_versioned"),
        CheckConstraint(
            _versioned("label_definition", nullable=False), name="label_definition_versioned"
        ),
        CheckConstraint(
            _versioned("selection_summary", nullable=True), name="selection_summary_versioned"
        ),
        CheckConstraint("frozen_by IS NULL OR frozen_by IN ('user', 'auto')", name="frozen_by"),
        CheckConstraint("(frozen_by IS NULL) = (frozen_at IS NULL)", name="frozen_by_iff_frozen"),
    )


class CohortMember(Base):
    """One volume in a cohort, with its label."""

    __tablename__ = "cohort_members"

    member_id = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    cohort_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cohorts.cohort_id", ondelete="RESTRICT"),
        nullable=False,
    )
    study_uid = Column(Text, nullable=False)
    volume_series_instance_uid = Column(Text, nullable=False)
    subject_key = Column(Text, nullable=False)
    ordinal = Column(Integer, nullable=False)
    label = Column(JSONB(none_as_null=True), nullable=True)
    label_source = Column(Text, nullable=False, server_default="pending")
    label_confidence = Column(Text, nullable=True)
    label_provenance = Column(JSONB(none_as_null=True), nullable=True)
    clinical_data = Column(JSONB(none_as_null=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    cohort = relationship("Cohort", back_populates="members")

    __table_args__ = (
        ForeignKeyConstraint(
            ["study_uid", "volume_series_instance_uid"],
            ["volume_series.study_uid", "volume_series.volume_series_instance_uid"],
            name="fk_cohort_members_study_uid_volume_series",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "cohort_id", "volume_series_instance_uid", name="uq_cohort_members_cohort_id"
        ),
        UniqueConstraint(
            "cohort_id",
            "ordinal",
            name="uq_cohort_members_cohort_id_ordinal",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        # The tuple segmentation_pairs' member FK targets.
        UniqueConstraint(
            "cohort_id",
            "member_id",
            "volume_series_instance_uid",
            name="uq_cohort_members_cohort_id_member_id",
        ),
        CheckConstraint("ordinal > 0", name="ordinal_positive"),
        CheckConstraint("length(btrim(subject_key)) > 0", name="subject_key_not_blank"),
        CheckConstraint(
            "label_source IN ('pending', 'llm_extraction', 'manual_override', 'uploaded_targets')",
            name="label_source",
        ),
        CheckConstraint(
            "label_confidence IS NULL OR label_confidence IN ('high', 'medium', 'low', 'unlabeled')",
            name="label_confidence",
        ),
        CheckConstraint(
            "label_source <> 'pending' OR label IS NULL", name="pending_has_no_label"
        ),
        CheckConstraint(
            "label IS NULL OR jsonb_typeof(label) = 'object'", name="label_is_object"
        ),
        CheckConstraint(
            "label_provenance IS NULL OR (jsonb_typeof(label_provenance) = 'object' "
            f"AND pg_column_size(label_provenance) <= {DOMAIN_JSON_MAX_BYTES})",
            name="label_provenance_bounded",
        ),
        CheckConstraint(
            "clinical_data IS NULL OR (jsonb_typeof(clinical_data) = 'object' "
            f"AND pg_column_size(clinical_data) <= {CLINICAL_DATA_MAX_BYTES})",
            name="clinical_data_bounded",
        ),
        Index(
            "ix_cohort_members_study_uid_volume_series_instance_uid",
            "study_uid",
            "volume_series_instance_uid",
        ),
    )
