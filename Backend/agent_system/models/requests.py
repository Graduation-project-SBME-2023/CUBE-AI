from typing import Any

from pydantic import BaseModel, Field

SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"


class SessionControlRequest(BaseModel):
    session_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=SESSION_ID_PATTERN,
        description="Session ID",
    )
    restart: bool = Field(
        False,
        description="Explicitly discard persisted execution progress and start a new run",
    )


class PlanningRequest(BaseModel):
    message: str = Field(..., description="Initial message or clinical hypothesis from the user to start planning")
    session_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=SESSION_ID_PATTERN,
        description="Session ID",
    )
    do_lit_review: bool = Field(True, description="Whether to perform a literature review")
    restart: bool = Field(
        False,
        description="Explicitly replace an existing persisted plan",
    )


class WebSocketBroadcastRequest(BaseModel):
    type: str = Field(..., min_length=1, max_length=128)
    payload: Any
    session_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=SESSION_ID_PATTERN,
    )
