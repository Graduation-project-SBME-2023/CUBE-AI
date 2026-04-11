from pydantic import BaseModel, ConfigDict
from uuid import UUID
from typing import Optional, List
from .steps import PlanStepOut,PlanStepCreate, PlanStepUpdate

class PlanBase(BaseModel):
    state: Optional[str] = None
    rationale: Optional[str] = None



# -----------------------------------------------------
# Create Schema (client provides only hypothesis_id + state)
# study_goal is derived automatically from Hypothesis.title
# -----------------------------------------------------
class PlanCreate(BaseModel):
    state: Optional[str] = "not_confirmed"
    rationale: Optional[str] = None


# -----------------------------------------------------
# Update Schema (study_goal must never be edited by user)
# -----------------------------------------------------
class PlanUpdate(BaseModel):
    state: Optional[str] = None
    rationale: Optional[str] = None


# -----------------------------------------------------
# Output Schema
# study_goal appears here because this is server-generated
# -----------------------------------------------------
class PlanOut(BaseModel):
    id: UUID
    hypothesis_id: UUID
    study_goal: str
    state: Optional[str] = None
    rationale: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# -----------------------------------------------------
# Output Schema with Steps (nested output)
# -----------------------------------------------------
class PlanWithStepsOut(BaseModel):
    id: UUID
    hypothesis_id: UUID
    study_goal: str
    state: Optional[str] = None
    steps: List[PlanStepOut] = []
    rationale: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PlanCreateWithSteps(PlanCreate):
    steps: List[PlanStepCreate] = []


class PlanUpdateWithSteps(PlanUpdate):
    steps: Optional[List[PlanStepUpdate]] = None