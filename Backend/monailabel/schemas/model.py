from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class ModelBase(BaseModel):
    
    name: str = Field(alias='model_name')
    description: Optional[str] = None
    configuration: Optional[dict] = None
    type: str
    

class ModelCreate(ModelBase):
    pass

class Model(ModelBase):
    model_id: int
    created_at: datetime | None = None
    class Config:
        orm_mode = True