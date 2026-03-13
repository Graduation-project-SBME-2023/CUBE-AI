from pydantic import BaseModel, ConfigDict
from uuid import UUID
from typing import Optional


class PlanStepBase(BaseModel):
    title: str
    description: Optional[str] = None
    module: Optional[str] = None
    status: Optional[str] = "not_confirmed"
    logs: Optional[list] = []


class PlanStepCreate(PlanStepBase):
    pass  # order and id are auto-generated


class PlanStepUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    module: Optional[str] = None
    status: Optional[str] = None
    order: Optional[int] = None  # optional for reordering
    logs: Optional[list] = None


class PlanStepOut(PlanStepBase):
    id: UUID
    plan_id: UUID
    order: int
    model_config = ConfigDict(from_attributes=True)
