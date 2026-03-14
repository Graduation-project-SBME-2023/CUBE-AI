from datetime import datetime
from typing import Optional

from models.session import Session
from models.execution import AgentState, AgentStatus
from services.session_service import SessionService


class PipelineMonitor:
    AGENT_ORDER = [
        "study_population_planner_agent",
        "segmentation_agent",
        "feature_extraction_agent",
        "modeling_agent",
        "result_reporter_agent",
        "latex_reporter_agent",
    ]

    def __init__(self, session_service: SessionService, session: Session):
        self.session_service = session_service
        self.session = session
        self.agents: dict[str, AgentState] = {
            name: AgentState(name=name) for name in self.AGENT_ORDER
        }

        self._last_active: Optional[str] = None

    async def on_agent_active(self, source: str) -> None:
        """Call when an agent start doing something"""
        if source not in self.agents:
            return

        agent = self.agents[source]

        if self._last_active and self._last_active != source:
            await self._mark_completed(self._last_active)

        if agent.status == AgentStatus.PENDING:
            agent.status = AgentStatus.RUNNING
            agent.started_at = datetime.now()
            self._last_active = source
            await self._broadcast()

    async def on_tool_result(self, source: str, tool_name: str, content: str):
        """Inspect tool result for error signals."""
        if source not in self.agents:
            return

        lowered = content.lower()
        is_error = any(kw in lowered for kw in ["error", "exception", "failed", "traceback", "warning"])

        if is_error:
            agent = self.agents[source]
            agent.status = AgentStatus.FAILED
            agent.finished_at = datetime.now()
            agent.error = content[:300]
            self._last_active = source
            await self._broadcast()

    async def on_pipeline_complete(self):
        """Mark the last running agent and pipeline as done."""
        if self._last_active:
            await self._mark_completed(self._last_active)
        await self._broadcast()

    async def on_pipeline_error(self, error: Exception):
        """Mark whatever was running as failed."""
        if self._last_active and self.agents[self._last_active].status == AgentStatus.RUNNING:
            agent = self.agents[self._last_active]
            agent.status = AgentStatus.FAILED
            agent.finished_at = datetime.now()
            agent.error = str(error)
        await self._broadcast()

    async def _mark_completed(self, name: str) -> None:
        agent = self.agents[name]
        if agent.status == AgentStatus.RUNNING:
            agent.status = AgentStatus.COMPLETED
            agent.finished_at = datetime.now()

    async def _broadcast(self):
        # change this to match what the frontend expects, and what to update the db with.
        # add a new method to the session service to handle pipeline updates
        payload = {
            "agents": [
                {
                    "name": a.name,
                    "status": a.status.value,
                    "started_at": a.started_at.isoformat() if a.started_at else None,
                    "finished_at": a.finished_at.isoformat() if a.finished_at else None,
                    "error": a.error,
                }
                for a in self.agents.values()
            ]
        }
        await self.session_service._ws_send(
            "Pipeline_agent_status", payload, session_id=self.session.session_id
        )
