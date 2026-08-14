from sqlalchemy import Column, Integer, String, DateTime, text,UUID
from application_api.db.base import Base

class Patient(Base):
    __tablename__ = 'patients'

    patient_id =  Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text('gen_random_uuid()')
    )

    orthanc_patient_id = Column(String, unique=True, index=True, nullable=False)

    name = Column(String, nullable=False)
    birth_date = Column(DateTime, nullable=False)
    gender = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=True)