from typing import List, Optional, Union, Dict, Any
from pydantic import BaseModel, Field


# --- DOMAIN ENTITIES (State) ---

class ClinicalHypothesis(BaseModel):
    id: str = Field(default_factory=lambda: "")
    title: str = ""
    population: Union[str, dict] = ""
    intervention: Union[str, dict] = ""
    comparison: Union[str, dict] = ""
    outcome: Union[str, dict] = ""
    hypothesis: str = ""  # Free-text hypothesis statement
    question: str = ""  # Free-text research question
    clinical_context: str = ""
    relationship_type: str = ""
    state: str = "not_confirmed"
    message: Optional[str] = None


# --- CONTEXT AUGMENTATION & TASK MESSAGES ---

class ContextAugmentation(BaseModel):
    """Domain knowledge injected into each agent's task message."""
    knowledge_summary: str = ""  # Agent-relevant slice of the knowledge base
    literature_findings: str = ""  # Key findings from reviews/papers
    methodology_guidance: str = ""  # Relevant methods, best practices
    terminology: str = ""  # MeSH terms, controlled vocabulary
    sources_guide: str = ""  # Where to find more information


class TaskMessage(BaseModel):
    """Structured task payload for each execution agent — per arch.html spec."""
    task_id: str = ""  # Unique task identifier
    phase: str = ""  # Pipeline phase (e.g. "cohort_discovery", "segmentation")
    agent_id: str = ""  # Target agent ID (matches STATIC_NODES id)
    agent_title: str = ""  # Customized step title from planner
    instructions: str = ""  # Agent-specific instructions (hypothesis-contextualized)
    context_augmentation: ContextAugmentation = Field(default_factory=ContextAugmentation)
    hypothesis_summary: str = ""  # Compact hypothesis summary for the agent
    payload: Dict[str, Any] = Field(default_factory=dict)  # Agent-specific parameters


class StudyStep(BaseModel):
    id: str = ""
    title: str
    description: str
    module: Optional[str] = None
    task_message: Optional[TaskMessage] = None
    logs: List[str] = Field(default_factory=list)
    status: str = "pending"


class StudyPlan(BaseModel):
    id: str = ""
    hypothesis_id: str = ""
    study_goal: str = ""
    steps: List[StudyStep] = Field(default_factory=list)
    state: str = "not_confirmed"


class PlannerState(BaseModel):
    hypothesis: ClinicalHypothesis = Field(default_factory=ClinicalHypothesis)
    plan: StudyPlan = Field(default_factory=StudyPlan)
    knowledge_base: str = ""  # Full knowledge base text (cached)
    sources_guide: str = ""  # Sources guide text (cached)
    knowledge_sections: Dict[str, str] = Field(default_factory=dict)  # Structured sections from knowledge builder
    status: str = "idle"
