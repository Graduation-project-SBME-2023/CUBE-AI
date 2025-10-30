from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime

class PatientBase(BaseModel):
    name: str
    birth_date: date  # ISO format date
    gender: str | None = None

class PatientCreate(PatientBase):
    pass

class PatientUpdate(BaseModel):
    name: Optional[str] = None
    birth_date: datetime = None
    gender: Optional[str] = None


class Patient(PatientBase):
    patient_id: int
    created_at: datetime | None = None  # ISO format date-time

    class Config:
        orm_mode = True