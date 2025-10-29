from pydantic import BaseModel
from typing import Optional


class WorkflowDetailBase(BaseModel):
    task_name: str
    study_UID: str
    volume_series_instance_UID: str
    label_series_instance_UID: Optional[str] = None
    annotated: Optional[bool] = False
    train: Optional[bool] = True
    modality: Optional[str] = None


class WorkflowDetailCreate(WorkflowDetailBase):
    pass


class WorkflowDetailUpdate(BaseModel):
    label_series_instance_UID: Optional[str] = None
    annotated: Optional[bool] = None
    train: Optional[bool] = None
    modality: Optional[str] = None


class WorkflowDetailOut(WorkflowDetailBase):
    class Config:
        orm_mode = True
