
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from uuid import UUID
from typing import Optional

class ContextAugmentationBase(BaseModel):
    knowledge_summary:    Optional[str] = ""
    literature_findings:  Optional[str] = ""
    methodology_guidance: Optional[str] = ""
    terminology:          Optional[str] = ""
    sources_guide:        Optional[str] = ""
 
 
class ContextAugmentationCreate(ContextAugmentationBase):
    pass
 
 
class ContextAugmentationUpdate(BaseModel):
    knowledge_summary:    Optional[str] = None
    literature_findings:  Optional[str] = None
    methodology_guidance: Optional[str] = None
    terminology:          Optional[str] = None
    sources_guide:        Optional[str] = None
 
 
class ContextAugmentationRead(ContextAugmentationBase):
    id: UUID
    created_at: datetime
    model_config = {"from_attributes": True}