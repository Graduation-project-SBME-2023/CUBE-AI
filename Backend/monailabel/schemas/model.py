from pydantic import BaseModel, Field

class ModelBase(BaseModel):
    id: str = Field(alias='model_id')
    name: str = Field(alias='model_name')
    description: str | None = None
    configuration: dict | None = None
    created_at: str | None = None  # ISO format date-time

class ModelCreate(ModelBase):
    pass

class Model(ModelBase):
    class Config:
        orm_mode = True