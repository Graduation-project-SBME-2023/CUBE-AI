import asyncio
import copy
import logging
from typing import Awaitable, Callable, Dict, Any, List

from models.session import Session, SessionState
from models.plans import ClinicalHypothesis, StudyPlan, StudyStep, TaskMessage
from .session_store import SessionStore
from task_manager.task_manager import TaskManager
from clients.backend_client import BackendClient
from .execution_state import (
    execution_start_allowed,
    find_first_incomplete_step,
    normalize_step_status,
    planning_start_allowed,
)
from .resume_recovery import reset_interrupted_agent
from .best_effort import schedule_best_effort

logger = logging.getLogger(__name__)


class SessionExecutionConflict(RuntimeError):
    """Raised when a non-explicit start would overwrite persisted progress."""


class SessionPlanningConflict(RuntimeError):
    """Raised when a non-explicit planning request targets existing work."""


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
        self._resume_locks: dict[str, asyncio.Lock] = {}

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

    def _clear_session_input(self, session_id: str) -> None:
        clear_queue = getattr(self.ws, "clear_message_queue", None)
        if callable(clear_queue):
            clear_queue(session_id)

    @staticmethod
    def _has_persisted_planning_state(session: Session) -> bool:
        """Return True only when persistence contains actual planner output.

        A new dashboard launch now saves the user's free-text hypothesis as a
        draft before calling /plan. That draft has no PICO fields and must not
        trip the overwrite guard. Structured hypotheses and plans remain
        protected unless the caller explicitly requests a restart.
        """
        if session.plan is not None:
            return True

        hypothesis = session.hypothesis
        if hypothesis is None:
            return False

        return hypothesis.state == "confirmed" or any(
            bool(getattr(hypothesis, field, ""))
            for field in ("population", "intervention", "comparison", "outcome")
        )

    # ------------------------------------------------------------------
    # Task lifecycle — routes call these
    # ------------------------------------------------------------------

    async def start_planning(
        self,
        session_id: str,
        message: str,
        planner,
        *,
        restart: bool = False,
    ) -> None:
        logger.info(f"[{session_id}] Starting planning")
        session = await self.store.get_or_create_async(session_id)
        if session.planning_task and not session.planning_task.done():
            raise SessionPlanningConflict(
                f"Planning already running for session {session_id}"
            )
        if session.execution_task and not session.execution_task.done():
            raise SessionPlanningConflict(
                f"Execution already running for session {session_id}"
            )
        if not planning_start_allowed(
            has_persisted_state=self._has_persisted_planning_state(session),
            restart=restart,
        ):
            raise SessionPlanningConflict(
                f"Session {session_id} already contains persisted planning state"
            )

        self._clear_session_input(session_id)

        async def planner_with_failure_notification(
            active_session: Session,
            starting_message: str,
        ) -> None:
            try:
                await planner(active_session, starting_message)
            except asyncio.CancelledError:
                raise
            except Exception:
                user_message = (
                    "Planning stopped because the planning service encountered "
                    "an unrecoverable error. Please retry the request."
                )
                await self._ws_send(
                    "msg",
                    user_message,
                    session_id=active_session.session_id,
                )
                await self._ws_send(
                    "planning_failed",
                    {
                        "reason": "planner_exception",
                        "message": user_message,
                    },
                    session_id=active_session.session_id,
                )
                raise

        self.tasks.start_planning(
            session,
            message,
            planner_with_failure_notification,
        )

    async def start_execution(
        self,
        session_id: str,
        executor,
        *,
        restart: bool = False,
    ) -> None:
        logger.info(f"[{session_id}] Starting execution")
        session = await self.store.get_or_create_async(session_id)

        if session.execution_task and not session.execution_task.done():
            raise SessionExecutionConflict(
                f"Execution already running for session {session_id}"
            )

        if (
            session.plan is None
            or not session.plan.steps
            or str(session.plan.state or "").lower() != "confirmed"
        ):
            raise SessionExecutionConflict(
                f"Session {session_id} has no confirmed executable pipeline plan"
            )

        persisted_steps = []
        if session.plan is not None and session.plan.id:
            persisted_steps = await BackendClient.get_plan_steps(str(session.plan.id))
            if not persisted_steps and session.plan.steps:
                persisted_steps = [
                    {"status": plan_step.status}
                    for plan_step in session.plan.steps
                ]
            elif not persisted_steps and not restart:
                raise SessionExecutionConflict(
                    f"Session {session_id} has a persisted plan whose execution "
                    "state could not be verified"
                )

        if not execution_start_allowed(persisted_steps, restart=restart):
            raise SessionExecutionConflict(
                f"Session {session_id} already has persisted execution progress; "
                "an explicit restart is required"
            )

        # /run is a full start/restart. Manual suffix recovery is configured
        # only by resume_pipeline().
        session.resume_from_agent = None
        session.resume_agent_statuses = {}
        if restart:
            self._clear_session_input(session_id)
            await self.tasks.restart_execution(session, executor)
        else:
            await self.tasks.start_execution(session, executor)

    async def resume_pipeline(
        self,
        session_id: str,
        executor,
        *,
        workspace_root: str,
        reset_runtime_state: (
            Callable[[str, str], Awaitable[None]] | None
        ) = None,
    ) -> dict:
        """
        Safely restart a hydrated pipeline from its first incomplete step.

        This is intentionally separate from resume(), which only releases the
        pause event of an already-live execution task.
        """
        lock = self._resume_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            session = await self.store.get_or_create_async(session_id)
            if session.execution_task and not session.execution_task.done():
                raise SessionExecutionConflict(
                    f"Execution already running for session {session_id}"
                )
            if session.plan is None or not session.plan.id:
                raise SessionExecutionConflict(
                    f"Session {session_id} has no persisted plan to resume"
                )

            steps = await BackendClient.get_plan_steps(str(session.plan.id))
            if not steps:
                raise SessionExecutionConflict(
                    f"Persisted execution state for session {session_id} "
                    "could not be verified"
                )

            interrupted_step = find_first_incomplete_step(steps)
            if interrupted_step is None:
                raise SessionExecutionConflict(
                    f"Session {session_id} is already fully completed"
                )

            interrupted_agent = str(interrupted_step.get("module") or "").strip()
            interrupted_step_id = str(interrupted_step.get("id") or "").strip()
            if not interrupted_agent or not interrupted_step_id:
                raise SessionExecutionConflict(
                    "The first incomplete plan step has no executable module or ID"
                )

            try:
                if reset_runtime_state is not None:
                    await reset_runtime_state(session_id, interrupted_agent)
                cleared_artifacts = await asyncio.to_thread(
                    reset_interrupted_agent,
                    workspace_root,
                    session_id,
                    interrupted_agent,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise SessionExecutionConflict(
                    f"Could not safely clear interrupted artifacts for "
                    f"{interrupted_agent}: {exc}"
                ) from exc

            persisted = await BackendClient.update_plan_step(
                interrupted_step_id,
                {"status": "PENDING"},
            )
            if persisted is None:
                raise SessionExecutionConflict(
                    f"Could not persist the reset of {interrupted_agent}; "
                    "execution was not started"
                )

            initial_statuses: dict[str, str] = {}
            agents: list[dict] = []
            for step in steps:
                agent_name = str(step.get("module") or "").strip()
                if not agent_name:
                    continue
                status = (
                    "PENDING"
                    if str(step.get("id") or "") == interrupted_step_id
                    else normalize_step_status(step.get("status"))
                )
                initial_statuses[agent_name] = status
                agents.append(
                    {
                        "name": agent_name,
                        "status": status,
                        "started_at": None,
                        "finished_at": None,
                        "error": None,
                    }
                )

            for plan_step in session.plan.steps:
                if str(plan_step.id) == interrupted_step_id:
                    plan_step.status = "PENDING"

            session.resume_from_agent = interrupted_agent
            session.resume_agent_statuses = initial_statuses
            session.last_error = None

            await self._ws_send(
                "pipeline_agent_status",
                {"agents": agents},
                session_id=session_id,
            )
            await self._ws_send(
                "pipeline_resume_prepared",
                {
                    "interrupted_agent": interrupted_agent,
                    "cleared_artifacts": cleared_artifacts,
                },
                session_id=session_id,
            )

            self._clear_session_input(session_id)
            await self.tasks.restart_execution(session, executor)
            await self.update_state(session, SessionState.RUNNING)
            logger.info(
                "[%s] Manually resumed pipeline from %s",
                session_id,
                interrupted_agent,
            )
            return {
                "session_id": session_id,
                "status": "resume_started",
                "interrupted_agent": interrupted_agent,
                "cleared_artifacts": cleared_artifacts,
            }

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
        self._clear_session_input(session_id)
        asyncio.create_task(self._sync_state(session))

    async def restart_execution(self, session_id: str, executor) -> None:
        logger.info(f"[{session_id}] Restarting execution")
        session = await self.store.require_async(session_id)
        self._clear_session_input(session_id)
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
            self._ws_send(
                "state_update", {"state": state.name}, session_id=session.session_id
            ),
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
        logger.info(
            f"[{session.session_id}] Hypothesis {action} — title: {hypo_data.get('title', '?')!r}"
        )

        try:
            if existing is None:
                hypothesis = ClinicalHypothesis(**hypo_data)
                result = await BackendClient.create_hypothesis(
                    session.session_id, hypothesis
                )
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
            logger.debug(
                f"[{session.session_id}] Hypothesis {action}d [id={result.id}]"
            )
            await self._ws_send(
                "hypothesis",
                session.hypothesis.model_dump(),
                session_id=session.session_id,
            )
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
            await self._ws_send(
                "plan", {"steps": ui_steps}, session_id=session.session_id
            )
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
                    logger.error(
                        f"[{session.session_id}] Failed to confirm hypothesis in backend"
                    )
            except Exception as e:
                logger.exception(
                    f"[{session.session_id}] Error confirming hypothesis: {e}"
                )
        else:
            logger.warning(
                f"[{session.session_id}] confirm_hypothesis called with no hypothesis on session"
            )

        # await self._ws_send(
        #     "msg", "Hypothesis Confirmed.", session_id=session.session_id
        # )

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
                    logger.error(
                        f"[{session.session_id}] Failed to confirm plan in backend"
                    )
            except Exception as e:
                logger.exception(f"[{session.session_id}] Error confirming plan: {e}")
        else:
            logger.warning(
                f"[{session.session_id}] confirm_plan called with no plan on session"
            )

        await self.update_state(session, SessionState.READY)
        # await self._ws_send(
        #     "msg", "Study Plan Confirmed.", session_id=session.session_id
        # )

    # ------------------------------------------------------------------
    # Queries — routes call these
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> Session | None:
        return self.store.get(session_id)

    def require_session(self, session_id: str) -> Session:
        return self.store.require(session_id)

    async def async_get_session(self, session_id: str) -> Session | None:
        return await self.store.get_async(session_id)

    async def async_require_session(self, session_id: str) -> Session | None:
        return await self.store.require_async(session_id)

    # ------------------------------------------------------------------
    # Execution sync — PipelineMonitor calls these
    # ------------------------------------------------------------------

    async def save_task_messages_for_plan(
        self,
        session: Session,
        task_messages: List[TaskMessage],
    ) -> List[Dict[str, Any]]:
        """
        Persist compiled task messages using step_id and then persist
        context augmentation by task_id.
        """
        if session.plan is None:
            logger.warning(
                f"[{session.session_id}] save_task_messages_for_plan called with no plan"
            )
            return []

        # Map module name -> step id for step-scoped persistence endpoint.
        step_index: Dict[str, str] = {
            (step.module or ""): str(step.id)
            for step in session.plan.steps
            if step.id and step.module
        }

        persisted: List[Dict[str, Any]] = []
        task_only_saved = 0

        for tm in task_messages:
            step_id = step_index.get(tm.agent_id)
            if not step_id:
                logger.warning(
                    f"[{session.session_id}] No step_id found for agent_id={tm.agent_id}; skipping task message persistence"
                )
                continue

            tm_payload = tm.model_dump()
            tm_result = await BackendClient.save_task_message_by_step_id(
                step_id, tm_payload
            )
            if not tm_result:
                continue

            task_only_saved += 1

            task_id = str(tm_result.get("task_id") or tm.task_id)
            context_payload = tm_payload.get("context_augmentation") or {}
            context_result = await BackendClient.save_context_augmentation_by_task_id(
                task_id,
                context_payload,
            )

            if context_result is None:
                logger.warning(
                    f"[{session.session_id}] Context augmentation save failed for task_id={task_id}"
                )
                continue

            persisted.append(
                {
                    "step_id": step_id,
                    "task_id": task_id,
                    "task_message": tm_result,
                    "context_augmentation": context_result,
                }
            )

        logger.info(
            f"[{session.session_id}] Persisted {len(persisted)}/{len(task_messages)} task messages with context augmentation "
            f"(task rows saved: {task_only_saved})"
        )
        return persisted

    async def read_task_message_by_step_id(self, step_id: str) -> Dict[str, Any] | None:
        """Read task message payload by plan step id."""
        return await BackendClient.get_task_message_by_step_id(step_id)

    async def read_context_augmentation_by_task_id(
        self, task_id: str
    ) -> Dict[str, Any] | None:
        """Read context augmentation payload by task id."""
        return await BackendClient.get_context_augmentation_by_task_id(task_id)

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
                "pipeline_agent_status",
                {"agents": agents},
                session_id=session.session_id,
            )
            return

        # Build a name→step_id index from the in-memory plan
        step_index: dict[str, str] = {
            s.module: str(s.id) for s in session.plan.steps if s.id and s.module
        }

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

            schedule_best_effort(
                lambda step_id=step_id, patch=copy.deepcopy(patch): (
                    BackendClient.update_plan_step(step_id, patch)
                ),
                name=f"pipeline-status:{agent['name']}",
            )

        await self._ws_send(
            "pipeline_agent_status", {"agents": agents}, session_id=session.session_id
        )

    async def update_agent_tool_logs(
        self, session: Session, agent: str, log: Dict
    ) -> None:
        step = next(
            (step for step in session.plan.steps if step.module == agent),
            None,
        ) if session.plan else None
        body = {
            "status": "RUNNING",
            "log": log,
        }
        if step and step.id:
            schedule_best_effort(
                lambda _step_id=str(step.id), _body=copy.deepcopy(body): (
                    BackendClient.update_plan_step(_step_id, _body)
                ),
                name=f"tool-log:{agent}",
            )
        await self._ws_send(log.get("type"), log.get("payload"), log.get("session_id"))

    async def update_tool_views(self, session, agent, msg_type, payload):
        step = next(
            (step for step in session.plan.steps if step.module == agent),
            None,
        ) if session.plan else None
        if not step or not step.id:
            return

        body = {
            "status": "RUNNING",
            "log": {
                "type": msg_type,
                "payload": payload,
                "session_id": session.session_id,
            },
        }

        schedule_best_effort(
            lambda _step_id=str(step.id), _body=copy.deepcopy(body): (
                BackendClient.update_plan_step(_step_id, _body)
            ),
            name=f"tool-view:{agent}:{msg_type}",
        )

    async def persist_agent_status(
        self,
        session_id: str,
        agent_name: str,
        status: str,
    ) -> None:
        """Persist a human-in-the-loop waiting/resolved state for hydration."""
        session = await self.store.get_async(session_id)
        if session is None or session.plan is None:
            logger.warning(
                f"[{session_id}] Cannot persist {agent_name}={status}: no hydrated plan"
            )
            return

        step = next(
            (
                plan_step
                for plan_step in session.plan.steps
                if plan_step.module == agent_name
            ),
            None,
        )
        if step is None or not step.id:
            logger.warning(
                f"[{session_id}] Cannot persist {agent_name}={status}: plan step not found"
            )
            return

        await BackendClient.update_plan_step(str(step.id), {"status": status})
        step.status = status

    async def recover_execution_state(self, session_id: str) -> dict | None:
        """
        Called by a route when the frontend reconnects.
        Fetches persisted step statuses and returns a pipeline_agent_status
        shaped payload the frontend can replay directly.
        """
        session = await self.store.get_async(session_id)
        if session is None or session.plan is None:
            hypothesis = await BackendClient.get_hypothesis_by_chat(session_id)
            if not hypothesis:
                return None
            plan = await BackendClient.get_plan_by_hypothesis(hypothesis.id)
            if not plan:
                return None
            steps = await BackendClient.get_plan_steps(str(plan.id))
        else:
            steps = await BackendClient.get_plan_steps(str(session.plan.id))

        if not steps:
            return None

        agents = [
            {
                "name": s["module"],
                "status": s["status"],
                "started_at": None,  # not stored — only current status matters
                "finished_at": None,
                "error": s.get("logs", [{}])[-1].get("message")
                if s.get("logs")
                else None,
            }
            for s in steps
            if s.get("module")
        ]

        return {"agents": agents}

    async def get_hypothesis_history(self, limit: int = 20) -> list[dict]:
        """Fetch hypothesis history from the backend API."""
        return await BackendClient.get_hypothesis_history(limit)
