from pydantic import BaseModel

class RadiomicsBase(BaseModel):
   
    label_series_instance_uid: str
    features: dict
    
class RadiomicsCreate(RadiomicsBase):
    pass

class Radiomics(RadiomicsBase):
    id: int
    created_at: str | None = None  # ISO format date-time

    class Config:
        orm_mode = True