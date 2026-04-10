import asyncio
from .session import Session, SessionState
import logging

logger = logging.getLogger(__name__)


class TaskManager:
    def __init__(self):
        self.sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str) -> Session:
        if session_id not in self.sessions:
            self.sessions[session_id] = Session(session_id)
        return self.sessions[session_id]

    def start_planning(self, session_id: str, starting_message: str, planner):
        session = self.get_or_create(session_id)
        logger.info(
            '[HypothesisFlow][TaskManager] start_planning requested | session_id=%s | message_len=%s',
            session_id,
            len(starting_message or ''),
        )

        if session.planning_task and not session.planning_task.done():
            raise RuntimeError("Planning already running")

        session.cancel_event.clear()
        session.pause_event.set()
        session.state = SessionState.PLANNING

        session.planning_task = asyncio.create_task(
            self._run_planning(session, starting_message, planner)
        )

    async def _run_planning(self, session: Session, starting_message: str, planner):
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

    async def start_execution(self, session_id: str, executor):
        session = self.get_or_create(session_id)

        # If planning is active, forcibly cancel it first
        if session.planning_task and not session.planning_task.done():
            session.interrupted = True
            session.planning_task.cancel()
            try:
                await session.planning_task
            except asyncio.CancelledError:
                pass

        if session.execution_task and not session.execution_task.done():
            raise RuntimeError("Execution already running")

        session.cancel_event.clear()
        session.pause_event.set()
        session.state = SessionState.RUNNING

        session.execution_task = asyncio.create_task(
            self._run_execution(session, executor)
        )

    async def _run_execution(self, session: Session, executor):
        try:
            await executor(session)
            session.state = SessionState.COMPLETED
        except asyncio.CancelledError:
            session.state = SessionState.CANCELLED
        except Exception as e:
            session.last_error = str(e)
            logger.exception(f'Execution {session.session_id} failed, error: {e}')
            session.state = SessionState.FAILED
        finally:
            session.execution_task = None

    def pause(self, session_id: str):
        session = self.sessions[session_id]
        session.pause_event.clear()
        session.state = SessionState.PAUSED

    def resume(self, session_id: str):
        session = self.sessions[session_id]
        session.pause_event.set()
        session.state = SessionState.RUNNING

    def stop(self, session_id: str):
        session = self.sessions[session_id]
        session.cancel_event.set()

        if session.planning_task:
            session.planning_task.cancel()

        if session.execution_task:
            session.execution_task.cancel()

    async def restart_execution(self, session_id: str, executor):
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        # Cancel and wait for clean shutdown
        self.stop(session_id)
        if session.execution_task and not session.execution_task.done():
            try:
                await session.execution_task
            except (asyncio.CancelledError, Exception):
                pass

        # Reset state for fresh run
        session.cancel_event.clear()
        session.pause_event.set()
        session.state = SessionState.RUNNING
        session.last_error = None

        session.execution_task = asyncio.create_task(
            self._run_execution(session, executor)
        )
