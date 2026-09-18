from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from application_api.db.base import Base

# current. `origin` is required because the assisted and pipeline paths
# emit an identical SeriesDescription format, so the Orthanc sync cannot tell
# them apart by sniffing the string — it records `imported`, and a producer
# declares its own origin through POST /segmentations/declare-origin.
SEGMENTATION_ORIGINS = ("assisted", "pipeline", "imported")
ARCHIVE_STATUSES = ("available", "unavailable", "quarantined")


class Segmentation(Base):
    __tablename__ = 'segmentations'

    label_series_instance_uid = Column(String, nullable=False, primary_key=True)
    orthanc_series_id = Column(String, unique=True, index=True, nullable=False)

    # RESTRICT, not CASCADE: losing the source series in Orthanc
    # marks the archive row unavailable, it never deletes scientific history.
    volume_series_instance_uid = Column(
        String,
        ForeignKey("volume_series.volume_series_instance_uid", ondelete="RESTRICT"),
        nullable=False,
    )

    # Renamed from the mislabelled `segmentation_type`: this holds the DICOM
    # SeriesDescription, not a type.
    series_description = Column(String, nullable=False)
    # Exact clinical target stored in the DICOM SegmentLabel.  Historical
    # records may be null until Orthanc is re-synchronised.
    target_label = Column(String, nullable=True, index=True)
    # Renamed from `method`.
    generation_method = Column(String, nullable=True)

    origin = Column(String, nullable=False, server_default="imported")
    # NULL means "not known", never "not empty" — nothing backfills False,
    # because False is what would auto-permit reuse of an unverified mask.
    is_empty_mask = Column(Boolean, nullable=True)

    model_id = Column(
        UUID(as_uuid=True),
        ForeignKey("models.model_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    archive_status = Column(String, nullable=False, server_default="available", index=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        # Supporting tuple: a segmentation pair's
        # candidate and selected SEG must be masks of the pair's own volume.
        UniqueConstraint(
            "label_series_instance_uid",
            "volume_series_instance_uid",
            name="uq_segmentations_label_series_instance_uid",
        ),
        CheckConstraint(
            "origin IN ('assisted', 'pipeline', 'imported')",
            name="origin",
        ),
        CheckConstraint(
            "archive_status IN ('available', 'unavailable', 'quarantined')",
            name="archive_status",
        ),
    )
