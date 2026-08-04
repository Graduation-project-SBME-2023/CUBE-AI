from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import date, datetime
from uuid import UUID

class PatientBase(BaseModel):
    name: str
    birth_date: Optional[date] = None  # ISO format date
    gender: str | None = None

class PatientCreate(PatientBase):
    pass

class PatientUpdate(BaseModel):
    name: Optional[str] = None
    birth_date: datetime = None
    gender: Optional[str] = None


class Patient(PatientBase):
    patient_id: UUID
    orthanc_patient_id: str | None = None
    created_at: datetime | None = None  # ISO format date-time
    age: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)