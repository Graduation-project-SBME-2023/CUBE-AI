from typing import List, Optional, Union, Dict, Any
from pydantic import BaseModel, Field
from dataclasses import dataclass

# --- DOMAIN ENTITIES (State) ---

# class ClinicalHypothesis(BaseModel):
#     id: str = Field(default_factory=lambda: "")
#     title: str = ""
#     population: Union[str, dict] = ""
#     intervention: Union[str, dict] = ""
#     outcome: Union[str, dict] = ""
#     clinical_context: str = ""
#     relationship_type: str = ""
#     state: str = "not_confirmed"
#     message: Optional[str] = None
#
# class StudyStep(BaseModel):
#     name: str
#     description: str
#     module: Optional[str] = None
#     logs: List[str] = Field(default_factory=list)
#     status: str = "pending"
#
# class StudyPlan(BaseModel):
#     hypothesis_id: str = ""
#     study_goal: str = ""
#     steps: List[StudyStep] = Field(default_factory=list)
#     state: str = "not_confirmed"
#
# class PlannerState(BaseModel):
#     hypothesis: ClinicalHypothesis = Field(default_factory=ClinicalHypothesis)
#     plan: StudyPlan = Field(default_factory=StudyPlan)
#     status: str = "idle"

# --- AGENT PROTOCOL (Events) ---

@dataclass
class UserInputMessage:
    content: str

@dataclass
class UIUpdateMessage:
    type: str # 'msg', 'hypothesis', 'plan'
    data: Any

@dataclass
class ExtractHypothesisRequest:
    user_query: str

@dataclass
class RefineHypothesisRequest:
    user_query: str
    current_hypothesis: Dict

@dataclass
class CreatePlanRequest:
    hypothesis: Dict

@dataclass
class RefinePlanRequest:
    user_query: str
    current_plan: Dict
    hypothesis: Dict

@dataclass
class MatchModulesRequest:
    plan: Dict
    module_descriptions: str

@dataclass
class WorkerResult:
    source: str # e.g., 'extractor', 'planner'
    data: Dict
    ui_message: str