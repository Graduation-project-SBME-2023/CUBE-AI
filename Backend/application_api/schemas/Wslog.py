from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from application_api.core.json_bounds import (
    WS_LOG_PAYLOAD_MAX_BYTES,
    JsonBoundsError,
    check_json,
)


class WSLogCreate(BaseModel):
    session_id: UUID
    msg_type: str = Field(min_length=1, max_length=255)
    payload: Any = None

    @field_validator("payload")
    @classmethod
    def _bounded(cls, value: Any) -> Any:
        try:
            return check_json(
                value, max_bytes=WS_LOG_PAYLOAD_MAX_BYTES, what="event payload"
            )
        except JsonBoundsError as exc:
            raise ValueError(str(exc)) from exc


class WSLog(BaseModel):
    """One session event. `event_id` is the replay cursor."""

    event_id: int
    session_id: UUID
    msg_type: str
    payload: Any = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
