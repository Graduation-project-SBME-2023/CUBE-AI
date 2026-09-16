from pydantic import BaseModel,field_validator, ConfigDict
from datetime import date, datetime
from typing import Optional
from uuid import UUID

class StudyBase(BaseModel):
    study_uid: str
    patient_id: UUID
    description: Optional[str] = None
    modality: Optional[str] = None
    created_at: Optional[date] = None  # ISO format date-time
    @field_validator("created_at", mode="before")
    @classmethod
    def extract_date(cls, v):
        if isinstance(v, datetime):
            return v.date()
        return v

class StudyCreate(StudyBase):
    pass

class StudyUpdate(BaseModel):
    description: Optional[str] = None
    modality: Optional[str] = None
    created_at: Optional[date] | None = None  # ISO format date-time

class StudyRead(StudyBase):
    orthanc_study_id: Optional[str] = None
    archive_status: str = "available"
    model_config = ConfigDict(from_attributes=True)

