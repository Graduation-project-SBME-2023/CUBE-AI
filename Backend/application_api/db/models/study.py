from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String
from sqlalchemy.sql import func
from application_api.db.base import Base
from sqlalchemy.dialects.postgresql import UUID


class Study(Base):
    __tablename__ = 'studies'

    study_uid = Column(String, primary_key=True, index=True)
    orthanc_study_id = Column(String, unique=True, index=True, nullable=False)
    # RESTRICT: deleting a patient must never silently delete the
    # studies, series, masks and radiomics rows derived from them.
    patient_id = Column(
        UUID(as_uuid=True),
        ForeignKey('patients.patient_id', ondelete="RESTRICT"),
        index=True,
    )
    description = Column(String, nullable=True)
    # A summary, not the truth for a multi-series study.
    modality = Column(String, nullable=True)

    archive_status = Column(String, nullable=False, server_default="available", index=True)

    # NOTE: existing rows may hold StudyDate here rather than ingestion time —
    # do not reinterpret it silently.
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
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
    )
