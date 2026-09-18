from pydantic import BaseModel,field_validator, ConfigDict
from datetime import date, datetime
from typing import Optional
from uuid import UUID

class StudyBase(BaseModel):
    study_uid: str
    patient_id: UUID
    description: Optional[str] = None
    modality: Optional[str] = None
    # The DICOM StudyDate/StudyTime, not row-insertion time.
    study_datetime: Optional[date] = None
    @field_validator("study_datetime", mode="before")
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
    study_datetime: Optional[date] | None = None

class StudyRead(StudyBase):
    orthanc_study_id: Optional[str] = None
    archive_status: str = "available"
    model_config = ConfigDict(from_attributes=True)

