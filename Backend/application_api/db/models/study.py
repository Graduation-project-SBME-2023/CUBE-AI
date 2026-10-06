from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.sql import func
from application_api.db.base import Base
from sqlalchemy.dialects.postgresql import UUID


class Study(Base):
    __tablename__ = 'studies'

    study_uid = Column(Text, primary_key=True)
    # Nullable for rows synced before the Orthanc ids existed (see Patient).
    orthanc_study_id = Column(String, unique=True, index=True, nullable=True)
    # RESTRICT: deleting a patient must never silently delete the
    # studies, series, masks and radiomics rows derived from them.
    patient_id = Column(
        UUID(as_uuid=True),
        ForeignKey('patients.patient_id', ondelete="RESTRICT"),
        nullable=False,
    )
    description = Column(Text, nullable=True)
    # A summary, not the truth for a multi-series study.
    modality = Column(String, nullable=True)

    archive_status = Column(Text, nullable=False, server_default="available", index=True)

    # Renamed from `created_at`: this holds the DICOM StudyDate/StudyTime
    # (falling back to sync time only when that tag is missing/unparseable),
    # never row-insertion time.
    study_datetime = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "archive_status IN ('available', 'unavailable', 'quarantined')",
            name="archive_status",
        ),
        Index(
            "ix_studies_description_trgm",
            "description",
            postgresql_using="gin",
            postgresql_ops={"description": "gin_trgm_ops"},
        ),
    )
