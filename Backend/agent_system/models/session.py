from dataclasses import dataclass, field
import asyncio
from enum import Enum, auto
from typing import Optional, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from services.pipeline_monitor import PipelineMonitor

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

    execution_monitor: "PipelineMonitor | None" = field(init=False, default=None)

    # Don't use default_factory for asyncio primitives
    pause_event: asyncio.Event = field(default=None)
    cancel_event: asyncio.Event = field(default=None)
    input_queue: asyncio.Queue = field(default=None)
    output_queue: asyncio.Queue = field(default=None)

    agent_status: Dict[str, Any] = field(default_factory=dict)
    last_error: Optional[str] = None
    interrupted: bool = False
    # Where a resume continues, identified by the STEP rather than by the
    # agent selector: two steps may name the same agent, and resuming "the
    # segmentation agent" would be ambiguous between them.
    resume_from_step_id: Optional[str] = None

    hypothesis: ClinicalHypothesis | None = None
    plan: StudyPlan | None = None

    def __post_init__(self):
        # Routes type session_id as either `str` (path params) or `UUID`
        # (Pydantic request bodies); this plain dataclass never coerces it
        # like Pydantic would. Every consumer downstream (WS queue keys,
        # SessionStore dict keys, JSON payloads) assumes a plain string, so
        # normalize once here rather than at each call site.
        self.session_id = str(self.session_id)

        # Create asyncio primitives here, inside the running event loop
        self.pause_event = asyncio.Event()
        self.cancel_event = asyncio.Event()
        self.input_queue = asyncio.Queue()
        self.output_queue = asyncio.Queue()

        self.pause_event.set()  # start unpaused
