from dataclasses import dataclass, field
import asyncio
from enum import Enum, auto
from typing import Optional, Dict, Any


class SessionState(Enum):
    IDLE = auto()
    PLANNING = auto()
    READY = auto()
    RUNNING = auto()
    PAUSED = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass
class Session:
    session_id: str

    planning_task: Optional[asyncio.Task] = None
    execution_task: Optional[asyncio.Task] = None

    state: SessionState = SessionState.IDLE

    # Control primitives
    pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    input_queue: asyncio.Queue = field(default_factory=asyncio.Queue)  # ??? needed?
    output_queue: asyncio.Queue = field(default_factory=asyncio.Queue)  # ??? needed?

    # Metadata - needed?
    agent_status: Dict[str, Any] = field(default_factory=dict)
    last_error: Optional[str] = None

    def __post_init__(self):
        self.pause_event.set()  # start unpaused
