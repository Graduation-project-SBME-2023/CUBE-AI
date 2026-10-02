import logging
from models.session import Session, SessionState
from clients.backend_client import BackendClient
from .execution_state import infer_recovered_session_state

logger = logging.getLogger(__name__)


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}

    # ------------------------------------------------------------------
    # Sync interface (for callers that can't await)
    # ------------------------------------------------------------------

    def get_or_create(self, session_id: str) -> Session:
        """Return the in-memory session if present, otherwise create a new shell. Callers that want full DB hydration should use get_or_create_async instead."""
        session_id = str(session_id)
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(session_id=session_id)
        return self._sessions[session_id]

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(str(session_id))

    def require(self, session_id: str) -> Session:
        """Get a session or raise KeyError if not found in memory."""
        session = self._sessions.get(str(session_id))
        if not session:
            raise KeyError(
                f"Session {session_id} not found in memory. "
                f"Use require_async to fall back to the DB."
            )
        return session

    # ------------------------------------------------------------------
    # Async interface (with DB fallback)
    # ------------------------------------------------------------------

    async def get_or_create_async(self, session_id: str) -> Session:
        """Return in-memory session if present. Otherwise fetch hypothesis + plan from the DB, hydrate a Session, cache it, and return it. Falls back to a bare shell if the DB has nothing for this ID."""
        # Routes type session_id as either `str` (path params) or `UUID`
        # (request bodies typed via Pydantic); the Session dataclass never
        # coerces it. Normalizing to str here keeps this dict's keys
        # consistent no matter which route created the session.
        session_id = str(session_id)
        if session_id in self._sessions:
            return self._sessions[session_id]

        session = await self._hydrate(session_id)
        self._sessions[session_id] = session
        return session

    async def get_async(self, session_id: str) -> Session | None:
        """
        Return in-memory session if present, otherwise try the DB.
        Returns None if neither source has a record.
        """
        session_id = str(session_id)
        if session_id in self._sessions:
            return self._sessions[session_id]

        hypothesis = await BackendClient.get_intent(session_id)
        if hypothesis is None:
            return None

        session = await self._hydrate(session_id)
        self._sessions[session_id] = session
        return session

    async def require_async(self, session_id: str) -> Session:
        """
        Like require(), but falls back to the DB before raising.
        Raises KeyError only if neither memory nor the DB has the session.
        """
        session = await self.get_async(session_id)
        if session is None:
            raise KeyError(
                f"Session {session_id} not found in memory or DB."
            )
        return session

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def put(self, session: Session) -> None:
        """Explicitly cache a session (e.g. after external construction)."""
        self._sessions[str(session.session_id)] = session

    def delete(self, session_id: str) -> None:
        self._sessions.pop(str(session_id), None)

    def all(self) -> dict[str, Session]:
        return dict(self._sessions)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _hydrate(self, session_id: str) -> Session:
        """
        Fetch hypothesis → then plan from the DB and populate a Session.
        Always returns a valid Session — fields are None if not found.
        """
        session = Session(session_id=session_id)

        try:
            hypothesis = await BackendClient.get_intent(session_id)
        except Exception as e:
            logger.error(f"[{session_id}] Failed to fetch hypothesis during hydration: {e}")
            return session

        if hypothesis is None:
            logger.info(f"[{session_id}] No hypothesis in DB — returning bare session")
            return session

        session.hypothesis = hypothesis
        logger.info(f"[{session_id}] Hydrated hypothesis [id={hypothesis.id}]")

        try:
            plan = await BackendClient.get_plan_by_session(session_id)
        except Exception as e:
            logger.error(f"[{session_id}] Failed to fetch plan during hydration: {e}")
            return session

        if plan is None:
            logger.info(f"[{session_id}] No plan in DB for hypothesis {hypothesis.id}")
            return session

        session.plan = plan
        logger.info(
            f"[{session_id}] Hydrated plan [id={plan.id}, steps={len(plan.steps)}]"
        )

        try:
            runs = await BackendClient.get_node_runs(session_id)
        except Exception as e:
            logger.error(
                f"[{session_id}] Failed to fetch agent runs during hydration: {e}"
            )
            runs = []

        if runs:
            # Execution state comes from `node_runs` now, so a hydrated
            # session reports what actually happened instead of assuming
            # nothing did. A persisted RUNNING row cannot mean an asyncio task
            # survived a process restart, so it hydrates as PAUSED: hydration
            # must never imply permission to create a replacement task.
            session.state = SessionState[infer_recovered_session_state(runs)]
            by_step = {str(run.get("node_id")): run for run in runs}
            for plan_step in session.plan.steps:
                run = by_step.get(str(plan_step.id))
                if run is None:
                    continue
                plan_step.run_id = str(run.get("run_id") or "")
                plan_step.run_status = str(run.get("status") or "pending")
                plan_step.started_at = run.get("started_at")
                plan_step.completed_at = run.get("completed_at")
                plan_step.error = (run.get("error") or {}).get("message")
        elif plan.state.lower() == "confirmed":
            session.state = SessionState.READY

        logger.info(
            f"[{session_id}] Hydrated safe session state: {session.state.name}"
        )
        return session
