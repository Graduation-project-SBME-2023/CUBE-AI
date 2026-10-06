import asyncio
import logging
from typing import Awaitable, Callable, Dict, Any, List

from models.session import Session, SessionState
from models.plans import ClinicalHypothesis, StudyPlan, StudyStep, TaskMessage
from .session_store import SessionStore
from task_manager.task_manager import TaskManager
from clients.backend_client import BackendClient
from .execution_state import planning_start_allowed
from .run_context import (
    PlannedRun,
    build_compiled_prompt,
    build_resolved_inputs,
    first_incomplete,
    planned_runs,
)

logger = logging.getLogger(__name__)


class SessionExecutionConflict(RuntimeError):
    """Raised when a non-explicit start would overwrite persisted progress."""


class SessionPlanningConflict(RuntimeError):
    """Raised when a non-explicit planning request targets existing work."""


# ---------------------------------------------------------------------------
# Per-step execution state
#
# `graph_nodes` has no `status` and no `logs` column — a step is a definition,
# and execution state belongs to `node_runs`. Between steps E and F this
# service kept it in memory, which meant a resume after a process restart could
# not be honoured. It is durable now: every function below reads
# `/runs/session/{id}`, so a fresh process reports exactly what the
# database says happened, and nothing has to be remembered to be recovered.
# ---------------------------------------------------------------------------


async def load_runs(session_id: str) -> list[PlannedRun]:
    """The session's approved runs, in node order. Empty means "not approved"."""
    return planned_runs(await BackendClient.get_node_runs(session_id))


# `sessions.control_state` holds control intent only: the user
# pausing or resuming. Progress (planning, running, completed, ...) is derived
# by the Application API and never written from here.
_PAUSED = "paused"
_ACTIVE = "active"


class SessionService:
    """The single coordination layer for all session-related operations."""

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

        Every session gets an empty draft `plans` row the instant it is
        created (the schema's 1:1 session/plan invariant) — so `session.plan
        is not None` is true for a session that has never been planned.
        Only a plan that actually has steps counts as persisted state.
        """
        if session.plan is not None and session.plan.steps:
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
        """Approve the plan if needed, then execute its runs."""
        logger.info(f"[{session_id}] Starting execution")
        session = await self.store.get_or_create_async(session_id)

        if session.execution_task and not session.execution_task.done():
            raise SessionExecutionConflict(
                f"Execution already running for session {session_id}"
            )

        if session.plan is None or not session.plan.steps:
            raise SessionExecutionConflict(
                f"Session {session_id} has no plan to execute"
            )

        runs = await load_runs(session_id)
        if not runs:
            # A draft plan owns no runs. Approving it creates exactly one per
            # step; a rejected approval (empty plan, unknown agent type) is a
            # conflict, not something to work around.
            approved = await BackendClient.approve_plan(session_id)
            if approved is None:
                raise SessionExecutionConflict(
                    f"The plan for session {session_id} could not be approved; "
                    "it was not executed."
                )
            session.plan = approved
            runs = await load_runs(session_id)
            if not runs:
                raise SessionExecutionConflict(
                    f"Session {session_id} was approved but has no agent runs; "
                    "reconciliation is required before it can execute."
                )

        started = [run for run in runs if run.status != "pending"]
        if started and not all(run.is_complete for run in started):
            raise SessionExecutionConflict(
                f"Session {session_id} has already-started agent runs. Resume it "
                "instead of starting it again — v1 runs each step exactly once."
            )
        if started and all(run.is_complete for run in runs):
            raise SessionExecutionConflict(
                f"Session {session_id} has already executed every step of its "
                "plan. A new run requires a new session (A21)."
            )
        if restart and started:
            raise SessionExecutionConflict(
                f"Session {session_id} cannot be restarted: v1 permits one run "
                "per step, so a re-execution requires a new session."
            )

        # Continue from the first step that has not completed. On a first run
        # that is step 1; after an interruption it is where the work stopped,
        # and **nothing is deleted to get there**.
        resume_point = first_incomplete(runs)
        session.resume_from_node_id = (
            resume_point.node_id if resume_point and started else None
        )

        self._clear_session_input(session_id)
        await self.tasks.start_execution(session, executor)
        # tasks.start_execution sets session.state = RUNNING synchronously
        # before scheduling the background task; persist that now instead of
        # leaving the DB status stuck at whatever it was created with.
        await self.update_state(session, session.state)
        if session.execution_task is not None:
            session.execution_task.add_done_callback(
                lambda _: asyncio.create_task(self._sync_state(session))
            )

    async def resume_pipeline(
        self,
        session_id: str,
        executor,
        *,
        workspace_root: str | None = None,
        reset_runtime_state: (
            Callable[[str, str], Awaitable[None]] | None
        ) = None,
    ) -> dict:
        """Continue an interrupted pipeline from its first incomplete step."""
        lock = self._resume_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            session = await self.store.get_or_create_async(session_id)
            if session.execution_task and not session.execution_task.done():
                raise SessionExecutionConflict(
                    f"Execution already running for session {session_id}"
                )

            runs = await load_runs(session_id)
            if not runs:
                raise SessionExecutionConflict(
                    f"Session {session_id} has no approved plan to resume. Approve "
                    "and start it instead."
                )

            interrupted = first_incomplete(runs)
            if interrupted is None:
                raise SessionExecutionConflict(
                    f"Session {session_id} is already fully completed"
                )
            if all(run.status == "pending" for run in runs):
                raise SessionExecutionConflict(
                    f"Session {session_id} has not started yet; start it instead of "
                    "resuming it."
                )

            # In-process waiters (a review long-poll, for example) are
            # runtime state, not results: clearing them is not deleting work.
            if reset_runtime_state is not None:
                await reset_runtime_state(session_id, interrupted.agent_type)

            session.resume_from_node_id = interrupted.node_id
            session.last_error = None

            status = await BackendClient.get_node_status(session_id)
            if status is not None:
                await self._ws_send(
                    "node_status", status, session_id=session_id
                )
            await self._ws_send(
                "pipeline_resume_prepared",
                {
                    "interrupted_agent": interrupted.agent_type,
                    "node_id": interrupted.node_id,
                    "run_id": interrupted.run_id,
                    "position": interrupted.position,
                    # Stated explicitly because the legacy response listed the
                    # artifacts it had just destroyed here.
                    "cleared_artifacts": [],
                },
                session_id=session_id,
            )

            self._clear_session_input(session_id)
            await self.tasks.restart_execution(session, executor)
            await self.update_state(session, SessionState.RUNNING)
            if session.execution_task is not None:
                session.execution_task.add_done_callback(
                    lambda _: asyncio.create_task(self._sync_state(session))
                )
            logger.info(
                "[%s] Resumed pipeline at step %s (%s), deleting nothing",
                session_id,
                interrupted.position,
                interrupted.agent_type,
            )
            return {
                "session_id": session_id,
                "status": "resume_started",
                "interrupted_agent": interrupted.agent_type,
                "node_id": interrupted.node_id,
                "run_id": interrupted.run_id,
                "cleared_artifacts": [],
            }

    def pause(self, session_id: str) -> None:
        logger.info(f"[{session_id}] Pausing")
        session = self.store.require(session_id)
        self.tasks.pause(session)
        asyncio.create_task(self._persist_control_state(session, _PAUSED))
        asyncio.create_task(self._sync_state(session))

    def resume(self, session_id: str) -> None:
        logger.info(f"[{session_id}] Resuming")
        session = self.store.require(session_id)
        self.tasks.resume(session)
        asyncio.create_task(self._persist_control_state(session, _ACTIVE))
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
        """Update the in-memory state and broadcast it. Progress is not persisted:
        the Application API derives it; only pause/resume intent is stored."""
        prev = session.state
        session.state = state
        logger.info(f"[{session.session_id}] State: {prev.name} -> {state.name}")
        await self._ws_send(
            "state_update", {"state": state.name}, session_id=session.session_id
        )

    async def _persist_control_state(self, session: Session, control_state: str) -> None:
        """Best-effort durable pause/resume; a failure is logged, never raised."""
        if await BackendClient.set_session_control_state(session.session_id, control_state) is None:
            logger.error(
                f"[{session.session_id}] Failed to persist control state '{control_state}'"
            )

    async def _sync_state(self, session: Session) -> None:
        await self.update_state(session, session.state)

    # ------------------------------------------------------------------
    # Hypothesis — planners call these
    # ------------------------------------------------------------------

    async def handle_hypothesis_update(
        self, session: Session, hypo_data: dict
    ) -> ClinicalHypothesis | None:
        """Create or update the hypothesis based on whether the session already has one. Returns the persisted object, or None if the backend call failed (task continues either way)."""
        existing = session.hypothesis
        action = "update" if existing is not None else "create"
        logger.info(
            f"[{session.session_id}] Hypothesis {action} — title: {hypo_data.get('title', '?')!r}"
        )

        try:
            if existing is None:
                hypothesis = ClinicalHypothesis(**hypo_data)
                result = await BackendClient.save_intent(
                    session.session_id, hypothesis
                )
            else:
                updated = existing.model_copy(update=hypo_data)
                result = await BackendClient.save_intent(session.session_id, updated)

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
        """Build and create/update the study plan. Requires session.hypothesis to already be set (handle_hypothesis_update must have succeeded first). Returns the persisted plan, or None on failure."""
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
                session_id=str(session.session_id),
                steps=[
                    StudyStep(
                        title=s.get("title", ""),
                        description=s.get("description", ""),
                        module=s.get("id", ""),
                    )
                    for s in steps_data
                ],
            )

            # A draft is unversioned: saving it is an idempotent replace of
            # the ordered step list, so create and update are the same call.
            plan = plan.model_copy(
                update={"id": str(session.session_id), "session_id": str(session.session_id)}
            )
            result = await BackendClient.save_plan_draft(session.session_id, plan)

            if result is None:
                logger.error(
                    f"[{session.session_id}] Plan {action} returned None — "
                    f"session.plan unchanged"
                )
                return None

            session.plan = result
            logger.debug(f"[{result.session_id}] Plan {action}d")

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
                result = await BackendClient.save_intent(
                    session.session_id, session.hypothesis
                )
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
        """Mark the planning phase finished — **not** the approval."""
        logger.info(f"[{session.session_id}] Plan draft ready for approval")

        if session.plan is None:
            logger.warning(
                f"[{session.session_id}] confirm_plan called with no plan on session"
            )
        await self.update_state(session, SessionState.READY)

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
        """Persist the compiled task messages onto the **draft** plan steps."""
        if session.plan is None:
            logger.warning(
                f"[{session.session_id}] save_task_messages_for_plan called with no plan"
            )
            return []

        by_agent: Dict[str, List[TaskMessage]] = {}
        for message in task_messages:
            by_agent.setdefault(message.agent_id, []).append(message)

        attached: List[Dict[str, Any]] = []
        for step in session.plan.steps:
            queued = by_agent.get(step.module or "")
            if not queued:
                logger.warning(
                    f"[{session.session_id}] No compiled task message for step "
                    f"{step.position} ({step.module}); it will execute on its "
                    "definition alone"
                )
                continue
            # Several steps may name one agent. They are filled in plan
            # order, one message each, instead of every step sharing the first.
            step.task_message = queued.pop(0)
            attached.append(
                {
                    "node_id": step.id,
                    "position": step.position,
                    "agent_type": step.module,
                    "task_id": step.task_message.task_id,
                }
            )

        saved = await BackendClient.save_plan_draft(session.session_id, session.plan)
        if saved is None:
            logger.error(
                f"[{session.session_id}] Compiled task messages were NOT persisted; "
                "the plan draft write failed"
            )
            return []
        session.plan = saved
        logger.info(
            f"[{session.session_id}] Persisted {len(attached)}/{len(task_messages)} "
            "compiled task message(s) onto the draft plan steps"
        )
        return attached

    async def handle_pipeline_update(
        self,
        session: Session,
        agents: list[dict],
    ) -> None:
        """Broadcast the in-flight agent view."""
        await self._ws_send(
            "node_status", {"agents": agents}, session_id=session.session_id
        )

    async def update_agent_tool_logs(
        self, session: Session, agent: str, log: Dict
    ) -> None:
        await self._ws_send(log.get("type"), log.get("payload"), log.get("session_id"))

    async def update_tool_views(self, session, agent, event_type, payload):
        await self._ws_send(event_type, payload, session.session_id)

    async def persist_agent_status(
        self,
        session_id: str,
        agent_name: str,
        status: str,
    ) -> None:
        """Announce a human-in-the-loop wait, and record it as an event."""
        await self._ws_send(
            "agent_waiting_for_user_input",
            {"agent": agent_name, "status": status},
            session_id=session_id,
        )

    async def update_current_agent(self, session: Session, run: PlannedRun) -> None:
        """Tell the UI which step is active. Live only — the durable record is
        the run's own `running` status, written when it was claimed."""
        await self._ws_send(
            "run_status",
            {
                "state": "RUNNING",
                "current_agent": run.agent_type,
                "node_id": run.node_id,
                "position": run.position,
                "run_id": run.run_id,
            },
            session_id=session.session_id,
        )

    async def report_pipeline_error(self, session: Session, error: Exception) -> None:
        """A failure outside any step (graph construction, cancellation)."""
        session.last_error = str(error)
        await self._ws_send(
            "run_stopped",
            {"reason": "pipeline_error", "error": str(error)[:2000]},
            session_id=session.session_id,
        )

    # ------------------------------------------------------------------
    # Run transitions — the runner's node wrapper calls these
    # ------------------------------------------------------------------

    async def claim_run(self, session: Session, run: PlannedRun) -> None:
        """Claim the run immediately before its step starts."""
        current = await load_runs(session.session_id)
        upstream = [item for item in current if item.position < run.position]
        this_run = next(
            (item for item in current if item.node_id == run.node_id), run
        )

        if this_run.compiled_prompt:
            # A reclaim continues the same run: its compiled prompt is already
            # fixed and may not be recompiled into something else.
            compiled_prompt = this_run.compiled_prompt
        else:
            compiled_prompt = build_compiled_prompt(this_run, upstream)

        session_row = await BackendClient.get_session(session.session_id) or {}
        resolved_inputs = build_resolved_inputs(this_run, upstream, session_row)

        result = await BackendClient.start_node_run(
            run.run_id, compiled_prompt, resolved_inputs
        )
        await self._broadcast_committed_event(session, result)

    async def finish_run(self, session: Session, run: PlannedRun) -> None:
        """Close the run when its step ends."""
        result = await BackendClient.complete_node_run(run.run_id)
        await self._broadcast_committed_event(session, result)

    async def fail_run(
        self, session: Session, run: PlannedRun, error: BaseException
    ) -> None:
        result = await BackendClient.fail_node_run(
            run.run_id,
            code=type(error).__name__,
            message=str(error) or type(error).__name__,
            details={"schema_version": 1, "position": run.position},
        )
        session.last_error = str(error)
        await self._broadcast_committed_event(session, result)

    async def _broadcast_committed_event(
        self, session: Session, transition: dict | None
    ) -> None:
        """Broadcast the event the Application API already committed."""
        event = (transition or {}).get("event")
        if not event:
            return
        await self.ws.send_ws_message(
            event.get("event_type"),
            event.get("payload"),
            session_id=session.session_id,
            event_id=event.get("event_id"),
        )

    async def recover_execution_state(self, session_id: str) -> dict | None:
        """Replay the per-agent status for a reconnecting frontend."""
        status = await BackendClient.get_node_status(session_id)
        if not status or not status.get("agents"):
            return None
        return status

    async def get_hypothesis_history(self, limit: int = 20) -> list[dict]:
        """Fetch hypothesis history from the backend API."""
        return await BackendClient.get_hypothesis_history(limit)
