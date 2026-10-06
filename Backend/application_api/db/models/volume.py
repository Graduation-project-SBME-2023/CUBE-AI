from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func
from application_api.db.base import Base


class Volume(Base):
    __tablename__ = 'volume_series'

    volume_series_instance_uid = Column(Text, primary_key=True)
    # Nullable for rows synced before the Orthanc ids existed (see Patient).
    orthanc_series_id = Column(String, unique=True, index=True, nullable=True)
    body_part_examined = Column(String, nullable=True)

    study_uid = Column(
        Text,
        ForeignKey('studies.study_uid', ondelete="RESTRICT"),
        nullable=False,
    )
    series_description = Column(Text, nullable=True)
    modality = Column(String, nullable=True)
    num_slices = Column(Integer, nullable=False)

    archive_status = Column(Text, nullable=False, server_default="available", index=True)

    # Renamed from `created_at`: this holds the DICOM SeriesDate/SeriesTime
    # (falling back to sync time only when that tag is missing/unparseable),
    # never row-insertion time.
    series_datetime = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # Supporting tuple for the membership/composite FKs:
        # a child row's volume must belong to the study it claims.
        UniqueConstraint(
            "study_uid",
            "volume_series_instance_uid",
            name="uq_volume_series_study_uid_volume_series_instance_uid",
        ),
        CheckConstraint(
            "archive_status IN ('available', 'unavailable', 'quarantined')",
            name="archive_status",
        ),
        Index("idx_series_study_id", "study_uid"),
        Index(
            "ix_volume_series_description_trgm",
            "series_description",
            postgresql_using="gin",
            postgresql_ops={"series_description": "gin_trgm_ops"},
        ),
    )
