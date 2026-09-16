from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from application_api.core.json_bounds import (
    DOMAIN_JSON_MAX_BYTES,
    JsonBoundsError,
    check_json,
)


class PlanStepBase(BaseModel):
    title: str = Field(min_length=1)
    # The agent that runs this step. No catalog FK: agent implementations are
    # code, and the service validates this against AGENT_FACTORIES before
    # admission. The same key may appear in many steps — each executes
    # separately, keyed by step_id.
    agent_type: str = Field(min_length=1)
    # The whole definition of what the step is supposed to do.
    # folded the old free-text `description` in here under "description";
    # this flow folded the compiled task message in under "task_message",
    # which is where it always belonged — it is part of the step definition,
    # not of the run that executes it.
    instructions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("instructions")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            return check_json(
                value, max_bytes=DOMAIN_JSON_MAX_BYTES, what="plan step instructions"
            )
        except JsonBoundsError as exc:
            raise ValueError(str(exc)) from exc


class PlanStepCreate(PlanStepBase):
    """step_order comes from list position; step_id is generated."""


class PlanStepUpdate(BaseModel):
    title: Optional[str] = None
    agent_type: Optional[str] = None
    step_order: Optional[int] = Field(default=None, gt=0)
    instructions: Optional[dict[str, Any]] = None

    @field_validator("instructions")
    @classmethod
    def _bounded(cls, value):
        if value is None:
            return value
        try:
            return check_json(
                value, max_bytes=DOMAIN_JSON_MAX_BYTES, what="plan step instructions"
            )
        except JsonBoundsError as exc:
            raise ValueError(str(exc)) from exc


class PlanStepOut(PlanStepBase):
    step_id: UUID
    session_id: UUID
    step_order: int

    model_config = ConfigDict(from_attributes=True)
