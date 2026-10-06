from sqlalchemy import CheckConstraint, Column, Date, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from application_api.db.base import Base

class Patient(Base):
    __tablename__ = 'patients'

    patient_id =  Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text('gen_random_uuid()')
    )

    # Nullable: rows synced before the Orthanc ids existed have none, and the
    # sync fills it in the next time it sees the patient.
    orthanc_patient_id = Column(String, unique=True, index=True, nullable=True)

    # Direct PHI mirrored from Orthanc. Storing it in plaintext is a
    # deliberate deployment decision, documented in
    # cubeai-docs/backend/database_audit.md ("PHI storage decision").
    name = Column(String, nullable=False)
    # Nullable: the Orthanc sync stores NULL when PatientBirthDate is absent or
    # unparseable (see repositories/orthanc_sync.py). DICOM PatientBirthDate
    # (DA) is date-only, so this column is DATE, not TIMESTAMPTZ.
    birth_date = Column(Date, nullable=True)
    gender = Column(String, nullable=True)

    # Orthanc loss marks the row unavailable instead of deleting it.
    archive_status = Column(Text, nullable=False, server_default="available", index=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # (closed 2026-09-15): kept — the Orthanc sync writer genuinely mutates
    # this row, so updated_at has a real reader.
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
            "ix_patients_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )
