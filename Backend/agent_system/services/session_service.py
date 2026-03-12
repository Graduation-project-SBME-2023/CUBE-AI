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

    async def _ws_send(self, event: str, data) -> None:
        """
        Best-effort WebSocket broadcast. Logs on failure, never raises.
        Use this everywhere instead of calling self.ws directly.
        """
        try:
            await self.ws.send_ws_message(event, data)
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
        logger.info(f"[{session.session_id}] State: {prev.name} → {state.name}")

        results = await asyncio.gather(
            BackendClient.update_session(session.session_id, {"state": state.name}),
            self._ws_send("state_update", {"state": state.name}),
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
            await self._ws_send("hypothesis", result.model_dump())
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
                        name=s.get("title", ""),
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
                    "name": s.name,
                    "description": s.description,
                    "module": s.module,
                }
                for s in result.steps
            ]
            await self._ws_send("plan", {"steps": ui_steps})
            return result

        except Exception as e:
            logger.exception(
                f"[{session.session_id}] Unexpected error during plan {action}: {e}"
            )
            return None

    # ------------------------------------------------------------------
    # Confirmation — called at end of planning loop
    # ------------------------------------------------------------------

    async def confirm_plan(self, session: Session) -> None:
        """
        Mark hypothesis and plan as confirmed, persist both, update state.
        Individual failures are logged but don't block the others.
        """
        logger.info(f"[{session.session_id}] Confirming hypothesis and plan")

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
            logger.warning(f"[{session.session_id}] confirm_plan called with no hypothesis on session")

        if session.plan is not None:
            session.plan.state = "confirmed"
            try:
                result = await BackendClient.update_plan(session.session_id, session.plan)
                if result:
                    session.plan = result
                else:
                    logger.error(f"[{session.session_id}] Failed to confirm plan in backend")
            except Exception as e:
                logger.exception(f"[{session.session_id}] Error confirming plan: {e}")
        else:
            logger.warning(f"[{session.session_id}] confirm_plan called with no plan on session")

        await self.update_state(session, SessionState.READY)
        await self._ws_send("msg", "Study Plan Confirmed.")

    # ------------------------------------------------------------------
    # Queries — routes call these
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> Session | None:
        return self.store.get(session_id)

    def require_session(self, session_id: str) -> Session:
        return self.store.require(session_id)