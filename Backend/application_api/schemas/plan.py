from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .steps import PlanStepCreate, PlanStepOut


class PlanOut(BaseModel):
    """A session's plan."""

    session_id: UUID
    approved_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PlanWithStepsOut(PlanOut):
    steps: List[PlanStepOut] = []

    model_config = ConfigDict(from_attributes=True)


class PlanDraftUpdate(BaseModel):
    """Replace the draft's ordered step list."""

    steps: List[PlanStepCreate]
