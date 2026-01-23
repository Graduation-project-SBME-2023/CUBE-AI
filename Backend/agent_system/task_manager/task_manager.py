import asyncio
from .session import Session, SessionState


class TaskManager:
    def __init__(self):
        self.sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str) -> Session:
        if session_id not in self.sessions:
            self.sessions[session_id] = Session(session_id)
        return self.sessions[session_id]

    def start_planning(self, session_id: str, starting_message: str, planner):
        session = self.get_or_create(session_id)

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
            await planner(session, starting_message)
            session.state = SessionState.READY
        except asyncio.CancelledError:
            session.state = SessionState.CANCELLED
        except Exception as e:
            session.last_error = str(e)
            session.state = SessionState.FAILED
        finally:
            session.planning_task = None

    def start_execution(self, session_id: str, executor):
        session = self.get_or_create(session_id)

        if session.state != SessionState.READY:
            raise RuntimeError("Session is not ready for execution")

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

    def restart_execution(self, session_id: str, executor):
        session = self.sessions[session_id]
        self.stop(session_id)
        self.start_execution(session_id, executor)
