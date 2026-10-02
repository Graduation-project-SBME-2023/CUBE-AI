from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .graph_nodes import GraphNodeCreate, GraphNodeOut


class SessionGraphOut(BaseModel):
    """A session's graph."""

    session_id: UUID
    approved_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class SessionGraphWithNodesOut(SessionGraphOut):
    nodes: List[GraphNodeOut] = []

    model_config = ConfigDict(from_attributes=True)


class SessionGraphDraftUpdate(BaseModel):
    """Replace the draft's ordered node list."""

    nodes: List[GraphNodeCreate]
