from typing import Any, Optional
from datetime import datetime
from pydantic import BaseModel


class WS_Log(BaseModel):
    uuid: Optional[str] = None
    session_id: str
    msg_type: str
    payload: Any
    created_at: Optional[datetime] = None
