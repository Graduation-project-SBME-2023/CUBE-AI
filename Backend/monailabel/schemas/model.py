from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional, Union, List, Dict, Any
from uuid import UUID

class ModelBase(BaseModel):
    name: str
    description: Optional[str] = None
    config: Dict[str, Any] = {}
    
    # Logic fields
    type: str  # You had this as nullable=False in SQLAlchemy
    modality: str  # "CT", "MRI", "any"
    dimension: Optional[int] = None
    
    # JSON fields
    anatomy: Optional[List[str]] = None  # e.g., ["spleen", "liver"]
    labels: Optional[Union[Dict[str, float], List[str]]] = None
    params_schema: Optional[Dict[str, Any]] = None

class ModelCreate(ModelBase):
    pass

class ModelUpdate(BaseModel):
    # Everything is Optional in an Update schema to allow partial updates (PATCH)
    name: Optional[str] = None
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    type: Optional[str] = None
    modality: Optional[str] = None
    dimension: Optional[int] = None
    anatomy: Optional[List[str]] = None
    labels: Optional[Union[Dict[str, float], List[str]]] = None
    params_schema: Optional[Dict[str, Any]] = None

class Model(ModelBase):
    model_id: UUID
    created_at: Optional[datetime] = None

    # Pydantic v2 way to enable ORM compatibility
    model_config = ConfigDict(from_attributes=True)