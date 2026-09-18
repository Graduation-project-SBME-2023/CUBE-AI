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
