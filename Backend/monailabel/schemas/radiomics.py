from pydantic import BaseModel

class RadiomicsBase(BaseModel):
    id: str
    label_series_instance_uid: str
    features: dict
    created_at: str | None = None  # ISO format date-time

class RadiomicsCreate(RadiomicsBase):
    pass

class Radiomics(RadiomicsBase):
    class Config:
        orm_mode = True