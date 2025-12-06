from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class ModelBase(BaseModel):
    
    model_name: str = Field(alias='model_name')
    description: Optional[str] = None
    configuration: Optional[dict] = None
    types: list[str] = Field(..., alias="types")
    

class ModelCreate(ModelBase):
    pass

class ModelUpdate(BaseModel):
    model_name: Optional[str] = None
    description: Optional[str] = None
    configuration: Optional[dict] = None
    types: Optional[list[str]] = None

class Model(ModelBase):
    model_id: int
    created_at: datetime | None = None
    class Config:
        orm_mode = True