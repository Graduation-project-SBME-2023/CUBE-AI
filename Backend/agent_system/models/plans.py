from typing import List, Optional, Union
from pydantic import BaseModel, Field


# --- DOMAIN ENTITIES (State) ---

class ClinicalHypothesis(BaseModel):
    id: Optional[str] = None
    title: str = ""
    population: Union[str, dict] = ""
    intervention: Union[str, dict] = ""
    outcome: Union[str, dict] = ""
    clinical_context: str = ""
    relationship_type: str = ""
    state: str = "not_confirmed"
    message: Optional[str] = None


class StudyStep(BaseModel):
    name: str
    description: str
    module: Optional[str] = None
    logs: List[str] = Field(default_factory=list)
    status: str = "pending"


class StudyPlan(BaseModel):
    hypothesis_id: str = ""
    study_goal: str = ""
    steps: List[StudyStep] = Field(default_factory=list)
    state: str = "not_confirmed"


class PlannerState(BaseModel):
    hypothesis: ClinicalHypothesis = Field(default_factory=ClinicalHypothesis)
    plan: StudyPlan = Field(default_factory=StudyPlan)
    status: str = "idle"
