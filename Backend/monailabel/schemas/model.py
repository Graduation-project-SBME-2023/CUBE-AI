from pydantic import BaseModel, Field

class ModelBase(BaseModel):
    
    name: str = Field(alias='model_name')
    description: str | None = None
    configuration: dict | None = None
    

class ModelCreate(ModelBase):
    pass

class Model(ModelBase):
    model_id: int
    created_at: str | None = None
    class Config:
        orm_mode = True