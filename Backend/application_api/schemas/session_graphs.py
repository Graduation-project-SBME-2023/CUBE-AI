from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .graph_changes import GraphChangeOut
from .graph_nodes import GraphNodeOut
from .node_runs import NodeRunOut
from .run_attempts import RunAttemptOut
from .sessions import Activity


class SessionGraphOut(BaseModel):
    session_id: UUID
    version: int
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class LiveNodeOut(BaseModel):
    """A live node with its current run and that run's latest attempt."""

    node: GraphNodeOut
    run: Optional[NodeRunOut] = None
    attempt: Optional[RunAttemptOut] = None


class NextStepOut(BaseModel):
    """What the Executor does next: start a node, resume its suspended attempt,
    or wait for the user to release it."""

    action: Literal["start", "resume", "await_release"]
    node_id: UUID


class SessionGraphView(SessionGraphOut):
    """GET /session-graphs/session/{id}: everything the Executor and the UI
    read to decide what happens next. ``next_step`` is ``None`` when nothing
    can happen until the graph, the session or an attempt changes."""

    nodes: list[LiveNodeOut] = Field(default_factory=list)
    pending_change: Optional[GraphChangeOut] = None
    activity: Activity
    next_step: Optional[NextStepOut] = None
