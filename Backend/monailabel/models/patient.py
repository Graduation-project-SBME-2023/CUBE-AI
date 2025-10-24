from sqlalchemy import Column, Integer, String, DateTime
from core.database import Base

class Patient(Base):
    __tablename__ = 'patients'

    patient_id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    birth_date = Column(DateTime, nullable=False)
    gender = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=True)