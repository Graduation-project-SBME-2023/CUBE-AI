from pydantic import BaseModel, ConfigDict
from typing import Optional

class WorkflowBase(BaseModel):
    task_name: str

class WorkflowCreate(WorkflowBase):
    pass

class WorkflowUpdate(BaseModel):
    pass
    

class Workflow(WorkflowBase):
    model_config = ConfigDict(from_attributes=True)