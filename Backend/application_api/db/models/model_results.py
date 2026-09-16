from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from application_api.db.base import Base
from application_api.db.models.cohorts import _versioned

MODEL_RESULT_STATUSES = ("building", "ready", "failed")


class ModelResult(Base):
    """One variant of one modeling comparison."""

    __tablename__ = "model_results"

    model_result_id = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_by_agent_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.agent_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    cohort_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cohorts.cohort_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    feature_set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("feature_sets.feature_set_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    comparison_key = Column(Text, nullable=False)
    variant_key = Column(Text, nullable=False)
    algorithm = Column(Text, nullable=False)
    parameters = Column(JSONB, nullable=False)
    preprocessing = Column(JSONB, nullable=False)
    feature_order = Column(JSONB, nullable=False)
    split_definition = Column(JSONB, nullable=False)
    seeds = Column(JSONB, nullable=False)
    environment = Column(JSONB, nullable=False)
    metrics = Column(JSONB(none_as_null=True), nullable=True)
    dataset_digest = Column(Text, nullable=False)
    predictions_artifact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    input_artifact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    selected = Column(Boolean, nullable=False, server_default=text("false"))
    model_file_name = Column(Text, nullable=True)
    model_file_digest = Column(Text, nullable=True)
    status = Column(Text, nullable=False, server_default="building")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "created_by_agent_run_id",
            "comparison_key",
            "variant_key",
            name="uq_model_results_created_by_agent_run_id",
        ),
        # The set belongs to this result's cohort.
        ForeignKeyConstraint(
            ["feature_set_id", "cohort_id"],
            ["feature_sets.feature_set_id", "feature_sets.cohort_id"],
            name="fk_model_results_feature_set_id_cohort_id",
            ondelete="RESTRICT",
        ),
        # One selected variant per comparison.
        Index(
            "uq_model_results_one_selected",
            "created_by_agent_run_id",
            "comparison_key",
            unique=True,
            postgresql_where=text("selected"),
        ),
        CheckConstraint("length(btrim(comparison_key)) > 0", name="comparison_key_not_blank"),
        CheckConstraint("length(btrim(variant_key)) > 0", name="variant_key_not_blank"),
        CheckConstraint("length(btrim(algorithm)) > 0", name="algorithm_not_blank"),
        CheckConstraint("status IN ('building', 'ready', 'failed')", name="status"),
        CheckConstraint("dataset_digest ~ '^[0-9a-f]{64}$'", name="dataset_digest_is_sha256"),
        CheckConstraint(
            "feature_set_id IS NOT NULL OR input_artifact_id IS NOT NULL",
            name="input_is_captured",
        ),
        CheckConstraint(
            "(model_file_name IS NULL) = (model_file_digest IS NULL)",
            name="model_file_both_or_neither",
        ),
        CheckConstraint(
            "model_file_digest IS NULL OR model_file_digest ~ '^[0-9a-f]{64}$'",
            name="model_file_digest_is_sha256",
        ),
        CheckConstraint(
            "model_file_name IS NULL OR (model_file_name !~ '^([a-zA-Z]:)?[\\\\/]' "
            "AND model_file_name !~ '(^|[\\\\/])\\.\\.([\\\\/]|$)')",
            name="model_file_name_is_relative",
        ),
        CheckConstraint("NOT selected OR status = 'ready'", name="selected_is_ready"),
        CheckConstraint("status <> 'ready' OR metrics IS NOT NULL", name="ready_has_metrics"),
        CheckConstraint(_versioned("parameters", nullable=False), name="parameters_versioned"),
        CheckConstraint(
            _versioned("preprocessing", nullable=False), name="preprocessing_versioned"
        ),
        CheckConstraint(
            _versioned("feature_order", nullable=False), name="feature_order_versioned"
        ),
        CheckConstraint(
            _versioned("split_definition", nullable=False), name="split_definition_versioned"
        ),
        CheckConstraint(_versioned("seeds", nullable=False), name="seeds_versioned"),
        CheckConstraint(_versioned("environment", nullable=False), name="environment_versioned"),
        CheckConstraint(_versioned("metrics", nullable=True), name="metrics_versioned"),
    )
