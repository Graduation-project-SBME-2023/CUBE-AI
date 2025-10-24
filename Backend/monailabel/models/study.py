from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from core.database import Base

class Study(Base):
    __tablename__ = 'studies'

    study_uid = Column(String, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey('patients.patient_id'), index=True)
    description = Column(String, nullable=True)
    modality = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=True)  # ISO format date-time