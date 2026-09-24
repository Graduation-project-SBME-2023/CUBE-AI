from uuid import UUID

from pydantic import BaseModel, Field


class SessionControlRequest(BaseModel):
    # current rejects non-UUID session ids at every API and WebSocket boundary: a
    # local-only identifier means an unrecoverable run that no row describes.
    session_id: UUID = Field(..., description="Session ID")
    restart: bool = Field(
        False,
        description="Explicitly discard persisted execution progress and start a new run",
    )


class PlanningRequest(BaseModel):
    message: str = Field(..., description="Initial message or clinical hypothesis from the user to start planning")
    session_id: UUID = Field(..., description="Session ID")
    do_lit_review: bool = Field(True, description="Whether to perform a literature review")
    restart: bool = Field(
        False,
        description="Explicitly replace an existing persisted plan",
    )


class SessionInputRequest(BaseModel):
    """Body for POST /session/{id}/input (WEBSOCKET_FIX_PLAN.md WS-10). The
    only thing this ever did over the WebSocket was push text into the
    planner's per-session input queue -- this is that, over REST, so a client
    retry (same Idempotency-Key header) can be made safe instead of silently
    racing a socket that might not be open (W18)."""

    text: str = Field(..., min_length=1, description="User-authored text for the session's input queue")
