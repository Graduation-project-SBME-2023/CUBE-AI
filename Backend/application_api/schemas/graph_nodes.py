from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GraphNodeOut(BaseModel):
    """A node and its current definition. Nodes are written only by applied
    graph changes; this is a read model."""

    node_id: UUID
    session_id: UUID
    node_seq: int
    position: int
    target_kind: str
    target_name: str
    target_version: str
    title: str
    params: dict[str, Any] = Field(default_factory=dict)
    input_sources: Optional[dict[str, Any]] = None
    expectations: Optional[dict[str, Any]] = None
    accepted_by_change_id: UUID
    removed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
