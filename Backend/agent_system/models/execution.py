from enum import Enum
from typing import Optional
from dataclasses import dataclass
from datetime import datetime


class AgentStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_FOR_RESOURCE = "WAITING_FOR_RESOURCE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class AgentState:
    name: str
    status: AgentStatus = AgentStatus.PENDING
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    resources: Optional[list[str]] = None
    queue_position: Optional[int] = None
