"""Training-job receipt schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class TrainingJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_name: str = Field(min_length=1)


class TrainingJobOut(BaseModel):
    job_id: str
    task_name: str
    created_at: datetime
    submitted_at: Optional[datetime] = None
    cancel_requested_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    outcome: Optional[str] = None
    error: Optional[dict[str, Any]] = None
    # The backend's last report, when it was reachable.
    backend_status: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
