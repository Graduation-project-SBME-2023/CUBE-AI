from typing import List, Optional, Union, Dict, Any
from pydantic import BaseModel, Field
from dataclasses import dataclass

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