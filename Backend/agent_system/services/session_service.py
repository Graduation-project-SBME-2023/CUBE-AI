import asyncio
import logging
from models.session import Session, SessionState
from models.plans import ClinicalHypothesis, StudyPlan, StudyStep
from .session_store import SessionStore
from task_manager.task_manager import TaskManager
from clients.backend_client import BackendClient

logger = logging.getLogger(__name__)


class SessionService:
    """
    The single coordination layer for all session-related operations.

    Owns:
      - SessionStore  (in-memory session registry)
      - TaskManager   (asyncio task lifecycle)
      - BackendClient (HTTP persistence)
      - ws_manager    (WebSocket broadcasts)

    Everything else (routes, planners, executors) should talk through here.
    Planners and executors receive a Session object and call back into this
    service for any state updates or persistence — inject `self` when
    building those callables (see start_planning / start_execution).
    """

    def __init__(self, ws_manager):
        self.store = SessionStore()
        self.tasks = TaskManager()
        self.ws = ws_manager

    # ------------------------------------------------------------------
    # Task lifecycle — routes call these
    # ------------------------------------------------------------------

    def start_planning(self, session_id: str, message: str, planner) -> None:
        """
        Fire-and-forget: creates the session if needed, then starts the
        planning asyncio task. The planner callable must accept
        (session: Session, message: str).
        """
        session = self.store.get_or_create(session_id)
        self.tasks.start_planning(session, message, planner)

    async def start_execution(self, session_id: str, executor) -> None:
        """
        Starts the execution asyncio task (cancels any active planning
        first). The executor callable must accept (session: Session).
        """
        session = self.store.get_or_create(session_id)
        await self.tasks.start_execution(session, executor)

    def pause(self, session_id: str) -> None:
        session = self.store.require(session_id)
        self.tasks.pause(session)
        # Fire-and-forget state sync — don't block the route handler
        asyncio.create_task(self._sync_state(session))

    def resume(self, session_id: str) -> None:
        session = self.store.require(session_id)
        self.tasks.resume(session)
        asyncio.create_task(self._sync_state(session))

    def stop(self, session_id: str) -> None:
        session = self.store.require(session_id)
        self.tasks.stop(session)
        asyncio.create_task(self._sync_state(session))

    async def restart_execution(self, session_id: str, executor) -> None:
        session = self.store.require(session_id)
        await self.tasks.restart_execution(session, executor)

    # ------------------------------------------------------------------
    # State updates — planners / executors call these
    # ------------------------------------------------------------------

    async def update_state(self, session: Session, state: SessionState) -> None:
        """
        Update session state in memory, persist to backend, and broadcast
        over WebSocket — all in one call.
        """
        session.state = state
        await asyncio.gather(
            BackendClient.update_session(
                session.session_id, {"state": state.name}
            ),
            self.ws.send_ws_message("state_update", {"state": state.name}),
            return_exceptions=True,   # don't let a WS/HTTP failure crash the task
        )

    async def _sync_state(self, session: Session) -> None:
        """Persist and broadcast the session's current state."""
        await self.update_state(session, session.state)

    # ------------------------------------------------------------------
    # Data operations — planners / executors call these
    # ------------------------------------------------------------------

    async def handle_hypothesis_update(self, session: Session, hypo_data: dict,) -> ClinicalHypothesis:
        """
        Called whenever the agent produces hypothesis data.
        Decided create vs update based on hypothesis ID.
        """
        exisiting = session.hypothesis

        if exisiting is None:
            hypothesis = ClinicalHypothesis(**hypo_data)
            result = await BackendClient.create_hypothesis(session.session_id, hypothesis)
            session.hypothesis = result
        else:
            updated = exisiting.model_copy(update=hypo_data)
            result = await BackendClient.update_hypothesis(updated)
            session.hypothesis = result

        await self.ws.send_ws_message("hypothesis", result.model_dump())
        return result

    async def handle_plan_update(
            self, session: Session, steps_data: list
    ) -> StudyPlan:
        plan = StudyPlan(
            hypothesis_id=session.hypothesis.id,  # service resolves this, not planner
            study_goal=session.hypothesis.title,
            steps=[
                StudyStep(
                    name=s.get("title", ""),
                    description=s.get("description", ""),
                    module=s.get("id", ""),
                )
                for s in steps_data
            ],
        )

        if session.plan is None:
            result = await BackendClient.create_plan(session.session_id, plan)
            session.plan = result
        else:
            plan = plan.model_copy(update={"id": session.plan.id})
            result = await BackendClient.update_plan(session.session_id, plan)
            session.plan = result

        ui_steps = [
            {"id": s.module, "name": s.name, "description": s.description, "module": s.module}
            for s in result.steps
        ]
        await self.ws.send_ws_message("plan", {"steps": ui_steps})
        return result

    async def create_plan(self, session: Session, plan: StudyPlan) -> StudyPlan | None:
        result = await BackendClient.create_plan(session.session_id, plan)
        if result:
            await self.ws.send_ws_message("plan_created", result.model_dump())
        return result

    async def update_plan(self, session: Session, plan: StudyPlan) -> StudyPlan | None:
        result = await BackendClient.update_plan(session.session_id, plan)
        if result:
            await self.ws.send_ws_message("plan_updated", result.model_dump())
        return result

    async def create_hypothesis(
        self, session: Session, hypothesis: ClinicalHypothesis
    ) -> ClinicalHypothesis | None:
        result = await BackendClient.create_hypothesis(session.session_id, hypothesis)
        if result:
            await self.ws.send_ws_message("hypothesis_created", result.model_dump())
        return result

    async def update_hypothesis(
        self, session: Session, hypothesis: ClinicalHypothesis
    ) -> ClinicalHypothesis | None:
        result = await BackendClient.update_hypothesis(hypothesis)
        if result:
            await self.ws.send_ws_message("hypothesis_updated", result.model_dump())
        return result

    # ------------------------------------------------------------------
    # Queries — routes call these
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> Session | None:
        return self.store.get(session_id)

    def require_session(self, session_id: str) -> Session:
        return self.store.require(session_id)