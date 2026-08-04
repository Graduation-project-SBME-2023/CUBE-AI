from pydantic import BaseModel, ConfigDict
from uuid import UUID
from typing import Optional

from datetime import datetime

class HypothesisBase(BaseModel):
    title: str
    population: Optional[str] = None
    intervention: Optional[str] = None
    outcome: Optional[str] = None
    clinical_context: Optional[str] = None
    relationship_type: Optional[str] = None
    state: Optional[str] = None
    hypothesis: Optional[str] = None
    column: Optional[str] = None
    comparison: Optional[str] = None


class HypothesisCreate(HypothesisBase):
    # chat_id: UUID
    pass


class HypothesisUpdate(BaseModel):
    title: Optional[str] = None
    population: Optional[str] = None
    intervention: Optional[str] = None
    outcome: Optional[str] = None
    clinical_context: Optional[str] = None
    relationship_type: Optional[str] = None
    state: Optional[str] = None

    hypothesis: Optional[str] = None
    column: Optional[str] = None
    comparison: Optional[str] = None

class HypothesisOut(HypothesisBase):
    id: UUID
    chat_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
