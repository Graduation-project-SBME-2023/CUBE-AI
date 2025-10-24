from pydantic import BaseModel
from typing import Optional

class WorkflowBase(BaseModel):
    task_name: str

class WorkflowCreate(WorkflowBase):
    pass

class WorkflowUpdate(BaseModel):
    pass
    

class Workflow(WorkflowBase):
    class Config:
        orm_mode = True