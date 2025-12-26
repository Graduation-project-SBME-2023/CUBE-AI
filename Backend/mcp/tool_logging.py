from dataclasses import dataclass, asdict, field
from typing import Any, Literal
import json
import time
import requests

EventType = Literal[
    "tool_started",
    "tool_progress",
    "tool_log",
    "tool_finished",
    "run_started",
    "run_finished",
    "error"
]

@dataclass
class ToolEvent:
    event_type: EventType
    source: str          # tool name or agent
    payload: Any
    timestamp: float = field(default_factory=time.time)

def log_tool_state(event: ToolEvent):
    requests.post(
        "http://localhost:8150/events",
        json=asdict(event),
        timeout=1
    )