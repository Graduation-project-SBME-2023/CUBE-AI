from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from uuid import UUID


class ModelBase(BaseModel):
    name: str
    description: Optional[str] = None
    config: Optional[dict] = None

    type: Optional[str] = None
    dimension: Optional[int] = None
    labels: Optional[dict] = None


class ModelCreate(ModelBase):
    pass


class ModelUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config: Optional[dict] = None

    type: Optional[str] = None
    dimension: Optional[int] = None
    labels: Optional[dict] = None


class Model(ModelBase):
    model_id: UUID
    created_at: Optional[datetime] = None

    class Config:
        orm_mode = True