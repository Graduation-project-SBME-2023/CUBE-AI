import asyncio
import logging
from typing import Dict

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

    Error handling philosophy:
      - Communication failures (HTTP, WebSocket) are logged and swallowed —
        they must never raise into a running task.
      - Missing/invalid session IDs raise immediately (caller's bug, not
        a runtime condition).
      - All public methods log at the appropriate level so failures are
        always traceable without crashing.
    """

    def __init__(self, ws_manager):
        self.store = SessionStore()
        self.tasks = TaskManager()
        self.ws = ws_manager

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ws_send(self, event: str, data, session_id) -> None:
        """
        Best-effort WebSocket broadcast. Logs on failure, never raises.
        Use this everywhere instead of calling self.ws directly.
        """
        try:
            await self.ws.send_ws_message(event, data, session_id=session_id)
        except Exception as e:
            logger.warning(f"WS broadcast failed [event={event}]: {e}")

    # ------------------------------------------------------------------
    # Task lifecycle — routes call these
    # ------------------------------------------------------------------

    def start_planning(self, session_id: str, message: str, planner) -> None:
        logger.info(f"[{session_id}] Starting planning")
        session = self.store.get_or_create(session_id)
        self.tasks.start_planning(session, message, planner)

    async def start_execution(self, session_id: str, executor) -> None:
        logger.info(f"[{session_id}] Starting execution")
        session = self.store.get_or_create(session_id)
        await self.tasks.start_execution(session, executor)

    def pause(self, session_id: str) -> None:
        logger.info(f"[{session_id}] Pausing")
        session = self.store.require(session_id)
        self.tasks.pause(session)
        asyncio.create_task(self._sync_state(session))

    def resume(self, session_id: str) -> None:
        logger.info(f"[{session_id}] Resuming")
        session = self.store.require(session_id)
        self.tasks.resume(session)
        asyncio.create_task(self._sync_state(session))

    def stop(self, session_id: str) -> None:
        logger.info(f"[{session_id}] Stopping")
        session = self.store.require(session_id)
        self.tasks.stop(session)
        asyncio.create_task(self._sync_state(session))

    async def restart_execution(self, session_id: str, executor) -> None:
        logger.info(f"[{session_id}] Restarting execution")
        session = self.store.require(session_id)
        await self.tasks.restart_execution(session, executor)

    # ------------------------------------------------------------------
    # State updates — planners / executors call these
    # ------------------------------------------------------------------

    async def update_state(self, session: Session, state: SessionState) -> None:
        """
        Update session state in memory, then best-effort persist + broadcast.
        The in-memory update always succeeds; HTTP/WS failures are logged only.
        """
        prev = session.state
        session.state = state
        logger.info(f"[{session.session_id}] State: {prev.name} -> {state.name}")

        results = await asyncio.gather(
            BackendClient.update_session(session.session_id, {"state": state.name}),
            self._ws_send("state_update", {"state": state.name}, session_id=session.session_id),
            return_exceptions=True,
        )

        if isinstance(results[0], Exception):
            logger.error(
                f"[{session.session_id}] Failed to persist state '{state.name}' "
                f"to backend: {results[0]}"
            )

    async def _sync_state(self, session: Session) -> None:
        await self.update_state(session, session.state)

    # ------------------------------------------------------------------
    # Hypothesis — planners call these
    # ------------------------------------------------------------------

    async def handle_hypothesis_update(
        self, session: Session, hypo_data: dict
    ) -> ClinicalHypothesis | None:
        """
        Create or update the hypothesis based on whether the session
        already has one. Returns the persisted object, or None if the
        backend call failed (task continues either way).
        """
        existing = session.hypothesis
        action = "update" if existing is not None else "create"
        logger.info(f"[{session.session_id}] Hypothesis {action} — title: {hypo_data.get('title', '?')!r}")

        try:
            if existing is None:
                hypothesis = ClinicalHypothesis(**hypo_data)
                result = await BackendClient.create_hypothesis(session.session_id, hypothesis)
            else:
                updated = existing.model_copy(update=hypo_data)
                result = await BackendClient.update_hypothesis(updated)

            if result is None:
                # BackendClient already logged the HTTP error details
                logger.error(
                    f"[{session.session_id}] Hypothesis {action} returned None — "
                    f"session.hypothesis unchanged"
                )
                return None

            session.hypothesis = result
            logger.debug(f"[{session.session_id}] Hypothesis {action}d [id={result.id}]")
            await self._ws_send("hypothesis", session.hypothesis.model_dump(), session_id=session.session_id)
            return result

        except Exception as e:
            logger.exception(
                f"[{session.session_id}] Unexpected error during hypothesis {action}: {e}"
            )
            return None

    # ------------------------------------------------------------------
    # Plan — planners call these
    # ------------------------------------------------------------------

    async def handle_plan_update(
        self, session: Session, steps_data: list
    ) -> StudyPlan | None:
        """
        Build and create/update the study plan. Requires session.hypothesis
        to already be set (handle_hypothesis_update must have succeeded first).
        Returns the persisted plan, or None on failure.
        """
        if session.hypothesis is None:
            logger.error(
                f"[{session.session_id}] handle_plan_update called but session has no "
                f"hypothesis yet — skipping"
            )
            return None

        action = "update" if session.plan is not None else "create"
        logger.info(
            f"[{session.session_id}] Plan {action} — "
            f"{len(steps_data)} step(s) for hypothesis {session.hypothesis.id}"
        )

        try:
            plan = StudyPlan(
                hypothesis_id=session.hypothesis.id,
                study_goal=session.hypothesis.title,
                steps=[
                    StudyStep(
                        title=s.get("title", ""),
                        description=s.get("description", ""),
                        module=s.get("id", ""),
                    )
                    for s in steps_data
                ],
            )

            if session.plan is None:
                result = await BackendClient.create_plan(plan)
            else:
                plan = plan.model_copy(update={"id": session.plan.id})
                result = await BackendClient.update_plan(plan)

            if result is None:
                logger.error(
                    f"[{session.session_id}] Plan {action} returned None — "
                    f"session.plan unchanged"
                )
                return None

            session.plan = result
            logger.debug(f"[{result.hypothesis_id}] Hypothesis' Plan {action}d")

            ui_steps = [
                {
                    "id": s.module,
                    "name": s.title,
                    "description": s.description,
                    "module": s.module,
                }
                for s in result.steps
            ]
            await self._ws_send("plan", {"steps": ui_steps}, session_id=session.session_id)
            return result

        except Exception as e:
            logger.exception(
                f"[{session.session_id}] Unexpected error during plan {action}: {e}"
            )
            return None

    # ------------------------------------------------------------------
    # Confirmation — called at end of planning loop
    # ------------------------------------------------------------------

    async def confirm_hypothesis(self, session: Session):
        logger.info(f"[{session.session_id}] Confirming the hypothesis")

        if session.hypothesis is not None:
            session.hypothesis.state = "confirmed"
            try:
                result = await BackendClient.update_hypothesis(session.hypothesis)
                if result:
                    session.hypothesis = result
                else:
                    logger.error(f"[{session.session_id}] Failed to confirm hypothesis in backend")
            except Exception as e:
                logger.exception(f"[{session.session_id}] Error confirming hypothesis: {e}")
        else:
            logger.warning(f"[{session.session_id}] confirm_hypothesis called with no hypothesis on session")

        await self._ws_send("msg", "Hypothesis Confirmed.", session_id=session.session_id)

    async def confirm_plan(self, session: Session) -> None:
        """
        Mark hypothesis and plan as confirmed, persist both, update state.
        Individual failures are logged but don't block the others.
        """
        logger.info(f"[{session.session_id}] Confirming plan")

        if session.plan is not None:
            session.plan.state = "confirmed"
            try:
                result = await BackendClient.update_plan(session.plan)
                if result:
                    session.plan = result
                else:
                    logger.error(f"[{session.session_id}] Failed to confirm plan in backend")
            except Exception as e:
                logger.exception(f"[{session.session_id}] Error confirming plan: {e}")
        else:
            logger.warning(f"[{session.session_id}] confirm_plan called with no plan on session")

        await self.update_state(session, SessionState.READY)
        await self._ws_send("msg", "Study Plan Confirmed.", session_id=session.session_id)

    # ------------------------------------------------------------------
    # Queries — routes call these
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> Session | None:
        return self.store.get(session_id)

    def require_session(self, session_id: str) -> Session:
        return self.store.require(session_id)

    # ------------------------------------------------------------------
    # Execution sync — PipelineMonitor calls these
    # ------------------------------------------------------------------

    async def handle_pipeline_update(
            self,
            session: Session,
            agents: list[dict],
    ) -> None:
        """
        Persist agent status changes to their corresponding plan steps,
        then broadcast to the frontend.

        Maps agent name → plan step by `module` field.
        Appends error text to the step's logs JSONB column if failed.
        """
        if session.plan is None:
            logger.warning(
                f"[{session.session_id}] handle_pipeline_update called with no plan"
            )
            await self._ws_send(
                "pipeline_agent_status", {"agents": agents}, session_id=session.session_id
            )
            return

        # Build a name→step_id index from the in-memory plan
        step_index: dict[str, str] = {
            s.module: str(s.id)
            for s in session.plan.steps
            if s.id and s.module
        }

        persist_tasks = []
        for agent in agents:
            step_id = step_index.get(agent["name"])
            if not step_id:
                continue  # agent not mapped to a plan step — skip

            patch: dict = {"status": agent["status"]}

            if agent.get("error"):
                patch["logs"] = [
                    {
                        "timestamp": agent.get("finished_at"),
                        "level": "error",
                        "message": agent["error"],
                    }
                ]

            persist_tasks.append(BackendClient.update_plan_step(step_id, patch))

        results = await asyncio.gather(*persist_tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"[{session.session_id}] Failed to persist step update: {result}"
                )

        await self._ws_send(
            "pipeline_agent_status", {"agents": agents}, session_id=session.session_id
        )

    async def update_agent_tool_logs(self, session: Session, agent: str, log: Dict) -> None:
        # use backend client first to update the logs of the specific step
        # then update the frontend via ws

        step_id = next(
            step.id for step in session.plan.steps
            if step.module == agent
        )
        body = {
            "status": "RUNNING",
            "log": log,
        }
        await BackendClient.update_plan_step(step_id, body)
        await self._ws_send(log.get('type'), log.get('payload'), log.get('session_id'))

    async def update_tool_views(self, session, agent, msg_type, payload):
        step_id = next(
            step.id for step in session.plan.steps
            if step.module == agent
        )

        body = {
            "status": "RUNNING",
            "log": {
                'type': msg_type,
                'payload': payload,
                'session_id': session.session_id
            },
        }

        await BackendClient.update_plan_step(step_id, body)

    async def recover_execution_state(self, session_id: str) -> dict | None:
        """
        Called by a route when the frontend reconnects.
        Fetches persisted step statuses and returns a pipeline_agent_status
        shaped payload the frontend can replay directly.
        """
        session = await self.store.get_async(session_id)
        if session is None or session.plan is None:
            return None

        steps = await BackendClient.get_plan_steps(str(session.plan.id))
        if not steps:
            return None

        agents = [
            {
                "name": s["module"],
                "status": s["status"],
                "started_at": None,  # not stored — only current status matters
                "finished_at": None,
                "error": s.get("logs", [{}])[-1].get("message") if s.get("logs") else None,
            }
            for s in steps
            if s.get("module")
        ]

        return {"agents": agents}
