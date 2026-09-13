from enum import Enum
from typing import Optional
from dataclasses import dataclass
from datetime import datetime


class AgentStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class AgentState:
    name: str
    status: AgentStatus = AgentStatus.PENDING
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
