from typing import Any, Optional
from datetime import datetime
from pydantic import BaseModel


class WS_Log(BaseModel):
    """One session event."""

    session_id: str
    msg_type: str
    payload: Any
    event_id: Optional[int] = None
    created_at: Optional[datetime] = None
