from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from application_api.core.json_bounds import FEATURE_MATRIX_MAX_BYTES
from application_api.db.base import Base
from application_api.db.models.cohorts import _versioned

FEATURE_SET_STATUSES = ("building", "frozen")


def _is_digest(column: str) -> str:
    return f"{column} ~ '^[0-9a-f]{{64}}$'"


class FeatureSet(Base):
    """The frozen feature matrix one feature-extraction run selected."""

    __tablename__ = "feature_sets"

    feature_set_id = Column(
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
    set_key = Column(Text, nullable=False)
    schema_version = Column(Integer, nullable=False, server_default="1")
    feature_names = Column(JSONB, nullable=False)
    feature_matrix = Column(JSONB, nullable=False)
    source_feature_ids = Column(JSONB(none_as_null=True), nullable=True)
    selection = Column(JSONB, nullable=False)
    extraction_config = Column(JSONB(none_as_null=True), nullable=True)
    analysis = Column(JSONB(none_as_null=True), nullable=True)
    quality_summary = Column(JSONB(none_as_null=True), nullable=True)
    config_digest = Column(Text, nullable=False)
    source_digest = Column(Text, nullable=False)
    manifest_digest = Column(Text, nullable=False)
    # Derived, not stored: `frozen_at IS NULL` means "building", else "frozen".
    # A physical `status` column used to duplicate this and could never
    # diverge from it (it was DB-CHECK-enforced), so it was removed; readers
    # get `status` as a computed value instead (see repositories/feature_sets.py).
    frozen_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "created_by_run_id", "set_key", name="uq_feature_sets_created_by_run_id"
        ),
        # The tuple model_results' composite FK targets, so a model result
        # cannot train on a set from another cohort.
        UniqueConstraint("feature_set_id", "cohort_id", name="uq_feature_sets_feature_set_id"),
        CheckConstraint("length(btrim(set_key)) > 0", name="set_key_not_blank"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint(
            "CASE WHEN jsonb_typeof(feature_names) = 'array' "
            "THEN jsonb_array_length(feature_names) > 0 ELSE false END",
            name="feature_names_is_array",
        ),
        CheckConstraint(
            "jsonb_typeof(feature_matrix) = 'object' "
            f"AND pg_column_size(feature_matrix) <= {FEATURE_MATRIX_MAX_BYTES}",
            name="feature_matrix_bounded",
        ),
        # Rule 1: rectangularity is a constraint, not a convention.
        CheckConstraint(
            "feature_matrix_is_rectangular(feature_names, feature_matrix)",
            name="feature_matrix_shape",
        ),
        CheckConstraint(
            "source_feature_ids IS NULL OR jsonb_typeof(source_feature_ids) = 'array'",
            name="source_feature_ids_is_array",
        ),
        CheckConstraint(_versioned("selection", nullable=False), name="selection_versioned"),
        # The field the report generator MUST read.
        CheckConstraint(
            "jsonb_typeof(selection->'label_supervised_on_full_data') = 'boolean'",
            name="selection_states_label_supervision",
        ),
        CheckConstraint(
            _versioned("extraction_config", nullable=True), name="extraction_config_versioned"
        ),
        CheckConstraint(_versioned("analysis", nullable=True), name="analysis_versioned"),
        CheckConstraint(
            _versioned("quality_summary", nullable=True), name="quality_summary_versioned"
        ),
        CheckConstraint(
            " AND ".join(
                _is_digest(column)
                for column in ("config_digest", "source_digest", "manifest_digest")
            ),
            name="digests_are_sha256",
        ),
    )
