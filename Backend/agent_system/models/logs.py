from typing import Any, Optional
from datetime import datetime
from pydantic import BaseModel


class SessionEvent(BaseModel):
    """One session event."""

    session_id: str
    event_type: str
    payload: Any
    event_id: Optional[int] = None
    created_at: Optional[datetime] = None
