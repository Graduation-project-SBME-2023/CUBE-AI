from pydantic import BaseModel, ConfigDict
from uuid import UUID
from typing import Optional

from .Taskmessage import TaskMessageCreate, TaskMessageUpdate, TaskMessageRead
class PlanStepBase(BaseModel):
    title: str
    description: Optional[str] = None
    module: Optional[str] = None
    status: Optional[str] = "not_confirmed"
    logs: Optional[list] = []
    instructions: Optional[dict] = None


class PlanStepCreate(PlanStepBase):
    pass  # step_order and id are auto-generated
    # Optionally dispatch a TaskMessage at creation time
    task_message: Optional[TaskMessageCreate] = None

class PlanStepUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    module: Optional[str] = None
    status: Optional[str] = None
    step_order: Optional[int] = None  # optional for reordering
    logs: Optional[list] = None
    instructions: Optional[dict] = None
    task_message: Optional[TaskMessageUpdate] = None

class PlanStepStatusUpdate(BaseModel):
    status: str
    log: Optional[dict] = None  # single log entry to append

class PlanStepOut(PlanStepBase):
    id: UUID
    plan_id: UUID
    step_order: int
    task_message: Optional[TaskMessageRead] = None

    model_config = ConfigDict(from_attributes=True)
