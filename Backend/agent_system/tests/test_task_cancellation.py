import asyncio
import logging
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2]
AGENT_SYSTEM_DIR = BACKEND_DIR / "agent_system"
for import_root in (str(BACKEND_DIR), str(AGENT_SYSTEM_DIR)):
    if import_root not in sys.path:
        sys.path.insert(0, import_root)

from models.session import Session, SessionState
from task_manager.task_manager import (
    UNEXPECTED_EXECUTION_CANCELLATION,
    TaskManager,
)
from agents.execution_agents.segmentation.pipelines.common import (
    SessionExecutionStateError,
    check_session_cancelled,
)


class TaskManagerCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_stop_remains_cancelled(self):
        manager = TaskManager()
        session = Session(session_id="explicit-stop")
        started = asyncio.Event()

        async def executor(_session):
            started.set()
            await asyncio.Event().wait()

        await manager.start_execution(session, executor)
        execution_task = session.execution_task
        await started.wait()

        manager.stop(session)
        await execution_task

        self.assertTrue(session.cancel_event.is_set())
        self.assertEqual(session.state, SessionState.CANCELLED)
        self.assertIsNone(session.last_error)

    async def test_internal_cancelled_error_is_reported_as_failed(self):
        manager = TaskManager()
        session = Session(session_id="internal-cancel")

        async def executor(_session):
            raise asyncio.CancelledError("dependency cancelled its task")

        with self.assertLogs(
            "task_manager.task_manager",
            level=logging.ERROR,
        ) as captured:
            await manager._run_execution(session, executor)

        self.assertEqual(session.state, SessionState.FAILED)
        self.assertEqual(
            session.last_error,
            UNEXPECTED_EXECUTION_CANCELLATION,
        )
        self.assertIn("cancelled internally", "\n".join(captured.output))

    async def test_restart_refuses_to_overlap_uncooperative_execution(self):
        manager = TaskManager()
        session = Session(session_id="stubborn-execution")
        started = asyncio.Event()
        release = asyncio.Event()

        async def executor(_session):
            started.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue

        await manager.start_execution(session, executor)
        original_task = session.execution_task
        await started.wait()

        with patch(
            "task_manager.task_manager.CANCELLATION_GRACE_SECONDS",
            0.02,
        ):
            with self.assertRaisesRegex(RuntimeError, "restart was aborted"):
                await manager.restart_execution(session, executor)

        self.assertIs(session.execution_task, original_task)
        self.assertGreaterEqual(original_task.cancelling(), 2)
        release.set()
        await original_task


class SegmentationCancellationGuardTests(unittest.TestCase):
    @staticmethod
    def _app_module_for(session):
        store = SimpleNamespace(get=lambda _session_id: session)
        return types.SimpleNamespace(
            session_service=SimpleNamespace(store=store),
        )

    def test_explicit_stop_raises_cancelled_error(self):
        session = Session(session_id="explicit-stop")
        session.state = SessionState.CANCELLED
        session.cancel_event.set()

        with patch.dict(
            sys.modules,
            {"app": self._app_module_for(session)},
        ):
            with self.assertRaises(asyncio.CancelledError):
                check_session_cancelled(session.session_id)

    def test_cancelled_state_without_stop_signal_is_internal_error(self):
        session = Session(session_id="stale-cancelled-state")
        session.state = SessionState.CANCELLED

        with patch.dict(
            sys.modules,
            {"app": self._app_module_for(session)},
        ):
            with self.assertRaisesRegex(
                SessionExecutionStateError,
                "without an explicit stop signal",
            ):
                check_session_cancelled(session.session_id)

    def test_failed_parent_execution_preserves_diagnostic(self):
        session = Session(session_id="failed-parent")
        session.state = SessionState.FAILED
        session.last_error = "agent team task ended unexpectedly"

        with patch.dict(
            sys.modules,
            {"app": self._app_module_for(session)},
        ):
            with self.assertRaisesRegex(
                SessionExecutionStateError,
                "agent team task ended unexpectedly",
            ):
                check_session_cancelled(session.session_id)


if __name__ == "__main__":
    unittest.main()
