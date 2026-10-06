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

from application_api.core.json_bounds import DOMAIN_JSON_MAX_BYTES
from application_api.db.base import Base

# The four digests that make an extraction content-addressed.
DIGEST_COLUMNS = ("config_digest", "image_digest", "segmentation_digest", "source_digest")


def _is_digest(column: str) -> str:
    return f"{column} ~ '^[0-9a-f]{{64}}$'"


class Radiomics(Base):
    """One extraction of one segment of one SEG."""

    __tablename__ = "radiomics_features"

    radiomics_feature_id = Column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="RESTRICT"),
        nullable=False,
    )
    label_series_instance_uid = Column(
        Text,
        ForeignKey("segmentation_masks.label_series_instance_uid", ondelete="RESTRICT"),
        nullable=False,
    )
    source_volume_uid = Column(Text, nullable=False)
    segment_number = Column(Integer, nullable=False)
    segmentation_pair_id = Column(
        UUID(as_uuid=True),
        ForeignKey("segmentation_pairs.pair_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_by_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("node_runs.run_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    features = Column(JSONB, nullable=False)
    extractor_name = Column(Text, nullable=False)
    extractor_version = Column(Text, nullable=False)
    configuration = Column(JSONB, nullable=False)
    config_digest = Column(Text, nullable=False)
    image_digest = Column(Text, nullable=False)
    segmentation_digest = Column(Text, nullable=False)
    source_digest = Column(Text, nullable=False)
    schema_version = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_radiomics_segmentation_id", "label_series_instance_uid"),
        # The content-addressed natural key.
        UniqueConstraint(
            "session_id",
            "label_series_instance_uid",
            "segment_number",
            "config_digest",
            "source_digest",
            name="uq_radiomics_features_session_id",
        ),
        # The measured SEG is a mask OF the source volume the row names.
        ForeignKeyConstraint(
            ["label_series_instance_uid", "source_volume_uid"],
            [
                "segmentation_masks.label_series_instance_uid",
                "segmentation_masks.volume_series_instance_uid",
            ],
            name="fk_radiomics_features_segmentation",
            ondelete="RESTRICT",
        ),
        # when a run produced the row, it is a run of the row's session.
        ForeignKeyConstraint(
            ["created_by_run_id", "session_id"],
            ["node_runs.run_id", "node_runs.session_id"],
            name="fk_radiomics_features_created_by_run_id_session_id",
            ondelete="RESTRICT",
        ),
        CheckConstraint("segment_number > 0", name="segment_number_positive"),
        CheckConstraint(
            " AND ".join(_is_digest(column) for column in DIGEST_COLUMNS),
            name="digests_are_sha256",
        ),
        CheckConstraint(
            "length(btrim(extractor_name)) > 0 AND length(btrim(extractor_version)) > 0",
            name="extractor_not_blank",
        ),
        CheckConstraint(
            "jsonb_typeof(features) = 'object' "
            f"AND pg_column_size(features) <= {DOMAIN_JSON_MAX_BYTES}",
            name="features_bounded",
        ),
        CheckConstraint(
            "configuration IS NULL OR (jsonb_typeof(configuration) = 'object' "
            f"AND pg_column_size(configuration) <= {DOMAIN_JSON_MAX_BYTES})",
            name="configuration_bounded",
        ),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
    )
