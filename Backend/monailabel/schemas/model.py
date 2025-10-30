from pydantic import BaseModel, Field
from datetime import datetime

class ModelBase(BaseModel):
    
    name: str = Field(alias='model_name')
    description: str | None = None
    configuration: dict | None = None
    

class ModelCreate(ModelBase):
    pass

class Model(ModelBase):
    model_id: int
    created_at: datetime | None = None
    class Config:
        orm_mode = True