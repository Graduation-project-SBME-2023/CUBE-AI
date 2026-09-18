from pydantic import BaseModel, Field


class SessionControlRequest(BaseModel):
    session_id: str = Field(..., description="Session ID")
    restart: bool = Field(
        False,
        description="Explicitly discard persisted execution progress and start a new run",
    )


class PlanningRequest(BaseModel):
    message: str = Field(..., description="Initial message or clinical hypothesis from the user to start planning")
    session_id: str = Field(..., description="Session ID")
    do_lit_review: bool = Field(True, description="Whether to perform a literature review")
    restart: bool = Field(
        False,
        description="Explicitly replace an existing persisted plan",
    )
