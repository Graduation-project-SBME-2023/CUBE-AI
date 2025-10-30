from pydantic import BaseModel

class StudyBase(BaseModel):
    study_uid: str
    patient_id: int
    description: str | None = None
    modaility: str | None = None
    created_at: str | None = None  # ISO format date-time

class StudyCreate(StudyBase):
    pass

class StudyUpdate(BaseModel):
    description: str | None = None
    modality: str | None = None
    created_at: str | None = None  # ISO format date-time

class Study(StudyBase):
    class Config:
        orm_mode = True

