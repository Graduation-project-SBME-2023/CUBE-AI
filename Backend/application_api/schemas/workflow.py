from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from typing import Any, Optional


class WorkflowBase(BaseModel):
    task_name: str


class WorkflowCreate(WorkflowBase):
    #  dataset_id is allocated server-side under a lock plus the UNIQUE
    # constraint. A client may not choose it.
    display_title: Optional[str] = None
    description: Optional[str] = None
    definition: dict[str, Any] = Field(default_factory=dict)


class WorkflowUpdate(BaseModel):
    display_title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    definition: Optional[dict[str, Any]] = None


class Workflow(WorkflowBase):
    dataset_id: int
    display_title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    definition: dict[str, Any] = Field(default_factory=dict)
    frozen_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
