# schemas/ws_log.py

from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime
from uuid import UUID


class WSLogBase(BaseModel):
    msg_type: Optional[str] = None
    payload: Optional[Any] = None   


class WSLogCreate(BaseModel):
    session_id: UUID
    msg_type: Optional[str] = None
    payload: Optional[Any] = None

class WSLogUpdate(BaseModel):
    msg_type: Optional[str] = None
    payload: Optional[Any] = None
class WSLog(WSLogBase):
    uuid: UUID
    session_id: UUID
    created_at: datetime

    class Config:
        orm_mode = True