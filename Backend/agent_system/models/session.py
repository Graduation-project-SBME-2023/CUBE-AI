from dataclasses import dataclass, field
import asyncio
from enum import Enum, auto
from typing import Optional, Dict, Any

from models.plans import ClinicalHypothesis, StudyPlan


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

    planning_task: Optional[asyncio.Task] = field(default=None)
    execution_task: Optional[asyncio.Task] = field(default=None)

    state: SessionState = field(default=SessionState.IDLE)

    # Don't use default_factory for asyncio primitives
    pause_event: asyncio.Event = field(default=None)
    cancel_event: asyncio.Event = field(default=None)
    input_queue: asyncio.Queue = field(default=None)
    output_queue: asyncio.Queue = field(default=None)

    agent_status: Dict[str, Any] = field(default_factory=dict)
    last_error: Optional[str] = None
    interrupted: bool = False

    hypothesis: ClinicalHypothesis | None = None
    plan: StudyPlan | None = None

    def __post_init__(self):
        # Create asyncio primitives here, inside the running event loop
        self.pause_event = asyncio.Event()
        self.cancel_event = asyncio.Event()
        self.input_queue = asyncio.Queue()
        self.output_queue = asyncio.Queue()

        self.pause_event.set()  # start unpaused
