from pydantic import BaseModel
from uuid import UUID
from typing import Optional, List
from .steps import PlanStepOut

class PlanBase(BaseModel):
    state: Optional[str] = None
    


# -----------------------------------------------------
# Create Schema (client provides only hypothesis_id + state)
# study_goal is derived automatically from Hypothesis.title
# -----------------------------------------------------
class PlanCreate(BaseModel):
    state: Optional[str] = "not_confirmed"


# -----------------------------------------------------
# Update Schema (study_goal must never be edited by user)
# -----------------------------------------------------
class PlanUpdate(BaseModel):
    state: Optional[str] = None


# -----------------------------------------------------
# Output Schema
# study_goal appears here because this is server-generated
# -----------------------------------------------------
class PlanOut(BaseModel):
    id: UUID
    hypothesis_id: UUID
    study_goal: str
    state: Optional[str] = None

    class Config:
        orm_mode = True


# -----------------------------------------------------
# Output Schema with Steps (nested output)
# -----------------------------------------------------
class PlanWithStepsOut(BaseModel):
    id: UUID
    hypothesis_id: UUID
    study_goal: str
    state: Optional[str] = None
    steps: List[PlanStepOut] = []

    class Config:
        orm_mode = True