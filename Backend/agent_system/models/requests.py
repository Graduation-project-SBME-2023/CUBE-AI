from pydantic import BaseModel, Field


class SessionControlRequest(BaseModel):
    session_id: str = Field(..., description="Session ID")


class PlanningRequest(BaseModel):
    message: str = Field(..., description="Initial message or clinical hypothesis from the user to start planning")
    session_id: str = Field(..., description="Session ID")
    do_lit_review: bool = Field(True, description="Whether to perform a literature review")
