from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from application_api.core.json_bounds import (
    DOMAIN_JSON_MAX_BYTES,
    JsonBoundsError,
    check_json,
)


class GraphNodeBase(BaseModel):
    title: str = Field(min_length=1)
    # The agent that runs this node. No catalog FK: agent implementations are
    # code, and the service validates this against AGENT_FACTORIES before
    # admission. The same key may appear in many nodes — each executes
    # separately, keyed by node_id.
    target_name: str = Field(min_length=1)
    # The whole definition of what the node is supposed to do.
    # folded the old free-text `description` in here under "description";
    # this flow folded the compiled task message in under "task_message",
    # which is where it always belonged — it is part of the node definition,
    # not of the run that executes it.
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            return check_json(
                value, max_bytes=DOMAIN_JSON_MAX_BYTES, what="graph node params"
            )
        except JsonBoundsError as exc:
            raise ValueError(str(exc)) from exc


class GraphNodeCreate(GraphNodeBase):
    """position comes from list position; node_id is generated."""


class GraphNodeUpdate(BaseModel):
    title: Optional[str] = None
    target_name: Optional[str] = None
    position: Optional[int] = Field(default=None, gt=0)
    params: Optional[dict[str, Any]] = None

    @field_validator("params")
    @classmethod
    def _bounded(cls, value):
        if value is None:
            return value
        try:
            return check_json(
                value, max_bytes=DOMAIN_JSON_MAX_BYTES, what="graph node params"
            )
        except JsonBoundsError as exc:
            raise ValueError(str(exc)) from exc


class GraphNodeOut(GraphNodeBase):
    node_id: UUID
    session_id: UUID
    position: int

    model_config = ConfigDict(from_attributes=True)
