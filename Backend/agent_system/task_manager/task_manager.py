import asyncio
import logging
from models.session import Session, SessionState
from services.timeout_policy import CANCELLATION_GRACE_SECONDS

logger = logging.getLogger(__name__)

UNEXPECTED_EXECUTION_CANCELLATION = (
    "Execution task was cancelled without an explicit stop request."
)


class TaskManager:
    """
    Pure asyncio task lifecycle manager.
    Has no knowledge of HTTP, WebSockets, or storage.
    All methods receive a Session object — fetching/creating sessions
    is the caller's responsibility (SessionService owns that).
    """

    def start_planning(self, session: Session, starting_message: str, planner) -> None:
        if session.planning_task and not session.planning_task.done():
            raise RuntimeError(f"Planning already running for session {session.session_id}")

        session.cancel_event.clear()
        session.pause_event.set()
        session.state = SessionState.PLANNING

        session.planning_task = asyncio.create_task(
            self._run_planning(session, starting_message, planner)
        )

    async def _run_planning(self, session: Session, starting_message: str, planner) -> None:
        try:
            logger.info(
                '[HypothesisFlow][TaskManager] _run_planning started | session_id=%s',
                session.session_id,
            )
            await planner(session, starting_message)
            session.state = SessionState.READY
            logger.info(
                '[HypothesisFlow][TaskManager] _run_planning finished successfully | session_id=%s',
                session.session_id,
            )
        except asyncio.CancelledError:
            if not session.interrupted:
                session.state = SessionState.CANCELLED
            logger.info(
                '[HypothesisFlow][TaskManager] _run_planning cancelled | session_id=%s | interrupted=%s',
                session.session_id,
                session.interrupted,
            )
        except Exception as e:
            session.last_error = str(e)
            logger.exception(f'Planning {session.session_id} failed, error: {e}')
            session.state = SessionState.FAILED
        finally:
            session.interrupted = False
            session.planning_task = None

    async def start_execution(self, session: Session, executor) -> None:
        # If planning is still active, cancel it cleanly before starting execution
        if session.planning_task and not session.planning_task.done():
            session.interrupted = True
            session.planning_task.cancel()
            try:
                await session.planning_task
            except asyncio.CancelledError:
                pass

        if session.execution_task and not session.execution_task.done():
            raise RuntimeError(f"Execution already running for session {session.session_id}")

        session.cancel_event.clear()
        session.pause_event.set()
        session.state = SessionState.RUNNING

        session.execution_task = asyncio.create_task(
            self._run_execution(session, executor)
        )

    async def _run_execution(self, session: Session, executor) -> None:
        try:
            await executor(session)
            session.state = SessionState.COMPLETED
        except asyncio.CancelledError:
            if session.cancel_event.is_set():
                session.state = SessionState.CANCELLED
                logger.info(
                    "Execution %s cancelled by an explicit stop request",
                    session.session_id,
                )
            else:
                # asyncio.CancelledError can also escape from an internal
                # dependency (for example, an agent/team task).  That is an
                # execution failure, not evidence that the user pressed Stop.
                session.last_error = UNEXPECTED_EXECUTION_CANCELLATION
                session.state = SessionState.FAILED
                logger.exception(
                    "Execution %s was cancelled internally without an "
                    "explicit stop request",
                    session.session_id,
                )
        except Exception as e:
            session.last_error = str(e)
            logger.exception(f'Execution {session.session_id} failed, error: {e}')
            session.state = SessionState.FAILED
        finally:
            session.execution_task = None
            session.resume_from_agent = None
            session.resume_agent_statuses = {}

    def pause(self, session: Session) -> None:
        session.pause_event.clear()
        session.state = SessionState.PAUSED

    def resume(self, session: Session) -> None:
        session.pause_event.set()
        session.state = SessionState.RUNNING

    def stop(self, session: Session) -> None:
        logger.info(
            "Explicit stop requested for session %s",
            session.session_id,
        )
        session.cancel_event.set()
        session.state = SessionState.CANCELLED
        if session.planning_task and not session.planning_task.done():
            session.planning_task.cancel()
        if session.execution_task and not session.execution_task.done():
            session.execution_task.cancel()

    async def restart_execution(self, session: Session, executor) -> None:
        # Cancel any running task and wait for clean shutdown
        self.stop(session)
        if session.execution_task and not session.execution_task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(session.execution_task),
                    timeout=CANCELLATION_GRACE_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Execution %s did not stop within %.1fs; refusing an overlapping restart",
                    session.session_id,
                    CANCELLATION_GRACE_SECONDS,
                )
                session.execution_task.cancel()
                raise RuntimeError(
                    "The previous execution did not stop cleanly; restart was aborted"
                )
            except (asyncio.CancelledError, Exception):
                pass

        # Reset state for a fresh run
        session.cancel_event.clear()
        session.pause_event.set()
        session.state = SessionState.RUNNING
        session.last_error = None

        session.execution_task = asyncio.create_task(
            self._run_execution(session, executor)
        )
