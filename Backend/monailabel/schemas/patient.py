from pydantic import BaseModel
from typing import Optional

class PatientBase(BaseModel):
    name: str
    birth_date: str  # ISO format date
    gender: str | None = None

class PatientCreate(PatientBase):
    pass

class PatientUpdate(BaseModel):
    name: Optional[str] = None
    birth_date: Optional[str] = None
    gender: Optional[str] = None


class Patient(PatientBase):
    patient_id: int
    created_at: str | None = None  # ISO format date-time

    class Config:
        orm_mode = True