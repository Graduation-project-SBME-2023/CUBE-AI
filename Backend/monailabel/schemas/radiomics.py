from pydantic import BaseModel
from datetime import datetime
from uuid import UUID

class RadiomicsBase(BaseModel):
   
    label_series_instance_uid: str
    features: dict
    
class RadiomicsCreate(RadiomicsBase):
    pass

class Radiomics(RadiomicsBase):
    id: UUID
    created_at: datetime | None = None  # ISO format date-time

    class Config:
        orm_mode = True