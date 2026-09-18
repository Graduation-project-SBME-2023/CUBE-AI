from sqlalchemy import Column, String, DateTime, text
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

    orthanc_patient_id = Column(String, unique=True, index=True, nullable=False)

    # Direct PHI mirrored from Orthanc. Storing it in plaintext is a
    # deliberate deployment decision, documented in
    # cubeai-docs/backend/database_audit.md ("PHI storage decision").
    name = Column(String, nullable=False)
    # Nullable: the Orthanc sync stores NULL when PatientBirthDate is absent or
    # unparseable (see repositories/orthanc_sync.py).
    birth_date = Column(DateTime(timezone=True), nullable=True)
    gender = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
