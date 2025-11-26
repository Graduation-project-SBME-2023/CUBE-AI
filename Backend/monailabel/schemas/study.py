from pydantic import BaseModel, validator
from datetime import date, datetime
from typing import Optional

class StudyBase(BaseModel):
    study_uid: str
    patient_id: int
    description: Optional[str] = None
    modality: Optional[str] = None
    created_at: Optional[date] = None  # ISO format date-time
    @validator("created_at", pre=True)
    def extract_date(cls, v):
        if isinstance(v, datetime):
            return v.date()  # keep only the date part
        return v

class StudyCreate(StudyBase):
    pass

class StudyUpdate(BaseModel):
    description: Optional[str] = None
    modality: Optional[str] = None
    created_at: Optional[date] | None = None  # ISO format date-time

class Study(StudyBase):
    class Config:
        orm_mode = True

