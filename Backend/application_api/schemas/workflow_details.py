from pydantic import BaseModel, ConfigDict, Field
from typing import Optional


class WorkflowDetailBase(BaseModel):
    task_name: str
    study_UID: str
    volume_series_instance_UID: str
    label_series_instance_UID: Optional[str] = None
    annotated: Optional[bool] = False
    train: Optional[bool] = True
    modality: Optional[str] = None
    # Deterministic training order. Multiple channels share case_index
    # and differ by channel_index.
    case_index: Optional[int] = Field(default=None, gt=0)
    channel_index: int = Field(default=0, ge=0)
    subject_key: Optional[str] = None


class WorkflowDetailCreate(WorkflowDetailBase):
    pass


class WorkflowDetailUpdate(BaseModel):
    label_series_instance_UID: Optional[str] = None
    annotated: Optional[bool] = None
    train: Optional[bool] = None
    modality: Optional[str] = None
    subject_key: Optional[str] = None


class WorkflowDetailOut(WorkflowDetailBase):
    model_config = ConfigDict(from_attributes=True)
