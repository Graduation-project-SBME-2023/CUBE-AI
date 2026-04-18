from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID
from typing import Any, Dict, Optional
from datetime import datetime
from uuid import UUID

from schemas.contextaugmentation import ContextAugmentationCreate, ContextAugmentationRead, ContextAugmentationUpdate

from schemas.contextaugmentation import ContextAugmentationCreate




class TaskMessageBase(BaseModel):
    phase:              Optional[str] = ""
    agent_title:        Optional[str] = ""
    instructions:       Optional[str] = ""
    hypothesis_summary: Optional[str] = ""
    payload:            Dict[str, Any] = Field(default_factory=dict)
 
 
class TaskMessageCreate(TaskMessageBase):
    # agent_id is set automatically from the parent PlanStep — not supplied by caller
    context_augmentation: ContextAugmentationCreate = Field(
        default_factory=ContextAugmentationCreate
    )
 
 
class TaskMessageUpdate(BaseModel):
    phase:              Optional[str]               = None
    agent_title:        Optional[str]               = None
    instructions:       Optional[str]               = None
    hypothesis_summary: Optional[str]               = None
    payload:            Optional[Dict[str, Any]]    = None
    context_augmentation: Optional[ContextAugmentationUpdate] = None
 
 
class TaskMessageRead(TaskMessageBase):
    id:                      UUID
    agent_id:                UUID           # == the PlanStep.id
    context_augmentation_id: Optional[UUID]
    context_augmentation:    Optional[ContextAugmentationRead] = None
    created_at:              datetime
    model_config = {"from_attributes": True}