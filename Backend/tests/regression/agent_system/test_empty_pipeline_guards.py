import json
import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock


BACKEND_DIR = Path(__file__).resolve().parents[3]
AGENT_SYSTEM_DIR = BACKEND_DIR / "agent_system"
for import_root in (str(BACKEND_DIR), str(AGENT_SYSTEM_DIR)):
    if import_root not in sys.path:
        sys.path.insert(0, import_root)

from agents.planning_agents.planner import (
    MAX_WS_MESSAGE_SIZE,
    ClinicalPlanner,
    _fit_literature_payload,
    _json_size_bytes,
)
from models.plans import PlannerState, StudyPlan, StudyStep
from models.session import Session
from services import session_service as session_service_module
from services.session_service import SessionExecutionConflict, SessionService


class _NoopWebSocketManager:
    async def send_ws_message(self, *_args, **_kwargs):
        return None


class PlannerEscapeRepairTests(unittest.TestCase):
    def test_repairs_latex_command_starting_with_u(self):
        malformed = r'{"instructions":"Use \usepackage{graphicx}"}'

        repaired = ClinicalPlanner._fix_invalid_json_escapes(malformed)

        self.assertEqual(
            json.loads(repaired)["instructions"],
            r"Use \usepackage{graphicx}",
        )

    def test_preserves_valid_unicode_escape(self):
        valid = r'{"label":"\u0041"}'

        repaired = ClinicalPlanner._fix_invalid_json_escapes(valid)

        self.assertEqual(repaired, valid)
        self.assertEqual(json.loads(repaired)["label"], "A")


class LiteraturePayloadBoundTests(unittest.TestCase):
    def test_large_payload_is_bounded_without_mutating_source(self):
        payload = {
            "knowledge_base": "evidence " * 12000,
            "sources_guide": "source " * 3000,
            "report": "report " * 8000,
            "papers": [
                {"title": f"Paper {index}", "abstract": "finding " * 500}
                for index in range(40)
            ],
            "hypothesis": {"title": "Test hypothesis"},
        }
        original_knowledge_base = payload["knowledge_base"]

        fitted = _fit_literature_payload(payload)

        self.assertLessEqual(_json_size_bytes(fitted), MAX_WS_MESSAGE_SIZE)
        self.assertEqual(payload["knowledge_base"], original_knowledge_base)
        self.assertIn("_truncated", fitted)

    def test_utf8_size_is_measured_in_bytes(self):
        value = {"text": "≥"}

        self.assertEqual(_json_size_bytes(value), len(json.dumps(value, ensure_ascii=False).encode("utf-8")))


class PipelineRevisionFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_revision_keeps_last_valid_plan(self):
        planner = ClinicalPlanner.__new__(ClinicalPlanner)
        planner.state = PlannerState(
            plan=StudyPlan(
                steps=[
                    StudyStep(
                        title="Cohort",
                        description="Select cohort",
                        module="study_population_planner_agent",
                    )
                ]
            )
        )
        planner.logger = logging.getLogger("test.invalid_revision")
        planner._parse_llm_json = AsyncMock(return_value=None)
        planner._send = AsyncMock()

        ready = await planner._process_pipeline_response("invalid response")

        self.assertTrue(ready)
        emitted_types = [call.args[0] for call in planner._send.await_args_list]
        self.assertEqual(emitted_types, ["msg"])

    async def test_invalid_initial_plan_emits_terminal_failure(self):
        planner = ClinicalPlanner.__new__(ClinicalPlanner)
        planner.state = PlannerState()
        planner.logger = logging.getLogger("test.invalid_initial_plan")
        planner._parse_llm_json = AsyncMock(return_value=None)
        planner._send = AsyncMock()

        ready = await planner._process_pipeline_response("invalid response")

        self.assertFalse(ready)
        emitted_types = [call.args[0] for call in planner._send.await_args_list]
        self.assertEqual(emitted_types, ["msg", "planning_failed"])


class ExecutionPlanGuardTests(unittest.IsolatedAsyncioTestCase):
    """What may start a run, now that runs are rows."""

    SESSION_ID = "33333333-3333-4333-8333-333333333333"

    def _service(self, runs=None, approve=None):
        service = SessionService(_NoopWebSocketManager())
        service.tasks.start_execution = AsyncMock()
        session_service_module.BackendClient.get_node_runs = AsyncMock(
            return_value=runs or []
        )
        session_service_module.BackendClient.approve_plan = AsyncMock(
            return_value=approve
        )
        return service

    def _session(self, steps=None):
        session = Session(session_id=self.SESSION_ID)
        session.plan = StudyPlan(
            session_id=self.SESSION_ID,
            steps=steps
            if steps is not None
            else [
                StudyStep(
                    title="Cohort",
                    description="Select the study cohort",
                    module="study_population_planner_agent",
                    id="step-1",
                    position=1,
                )
            ],
        )
        return session

    @staticmethod
    def _run(position, status, node_id=None):
        return {
            "run_id": f"run-{position}",
            "node_id": node_id or f"step-{position}",
            "session_id": ExecutionPlanGuardTests.SESSION_ID,
            "status": status,
            "node": {
                "node_id": node_id or f"step-{position}",
                "position": position,
                "target_name": "study_population_planner_agent",
                "title": "Cohort",
                "params": {},
            },
        }

    async def test_rejects_session_without_plan(self):
        service = self._service()
        service.store.put(Session(session_id=self.SESSION_ID))

        with self.assertRaisesRegex(SessionExecutionConflict, "no plan to execute"):
            await service.start_execution(self.SESSION_ID, self._executor)

    async def test_rejects_plan_with_no_steps(self):
        service = self._service()
        service.store.put(self._session(steps=[]))

        with self.assertRaisesRegex(SessionExecutionConflict, "no plan to execute"):
            await service.start_execution(self.SESSION_ID, self._executor)

    async def test_a_refused_approval_does_not_execute(self):
        service = self._service(runs=[], approve=None)
        service.store.put(self._session())

        with self.assertRaisesRegex(SessionExecutionConflict, "could not be approved"):
            await service.start_execution(self.SESSION_ID, self._executor)
        service.tasks.start_execution.assert_not_awaited()

    async def test_a_draft_plan_is_approved_and_then_executed(self):
        service = SessionService(_NoopWebSocketManager())
        service.tasks.start_execution = AsyncMock()
        session = self._session()
        service.store.put(session)

        approved = StudyPlan(
            session_id=self.SESSION_ID, steps=session.plan.steps, state="confirmed"
        )
        session_service_module.BackendClient.approve_plan = AsyncMock(
            return_value=approved
        )
        # No runs before approval, one pending after.
        session_service_module.BackendClient.get_node_runs = AsyncMock(
            side_effect=[[], [self._run(1, "pending")]]
        )

        await service.start_execution(self.SESSION_ID, self._executor)

        session_service_module.BackendClient.approve_plan.assert_awaited_once()
        service.tasks.start_execution.assert_awaited_once()
        self.assertIsNone(session.resume_from_node_id)

    async def test_a_completed_session_cannot_run_again(self):
        service = self._service(runs=[self._run(1, "completed")])
        service.store.put(self._session())

        with self.assertRaisesRegex(SessionExecutionConflict, "requires a new session"):
            await service.start_execution(self.SESSION_ID, self._executor)

    async def test_an_interrupted_session_must_be_resumed_not_restarted(self):
        service = self._service(runs=[self._run(1, "running")])
        service.store.put(self._session())

        with self.assertRaisesRegex(SessionExecutionConflict, "Resume it"):
            await service.start_execution(self.SESSION_ID, self._executor)
        service.tasks.start_execution.assert_not_awaited()

    @staticmethod
    async def _executor(_session):
        raise AssertionError("executor must not start")


class PlanningFailureNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unhandled_planner_failure_is_broadcast(self):
        service = SessionService(_NoopWebSocketManager())
        session = Session(session_id="planner-failure")
        service.store.put(session)
        events = []

        async def record_event(event, data, session_id):
            events.append((event, data, session_id))

        async def failing_planner(_session, _message):
            raise RuntimeError("upstream model unavailable")

        service._ws_send = record_event
        await service.start_planning(
            session.session_id,
            "test request",
            failing_planner,
        )
        planning_task = session.planning_task
        await planning_task

        self.assertEqual(session.last_error, "upstream model unavailable")
        self.assertEqual(session.state.name, "FAILED")
        self.assertEqual([event[0] for event in events], ["msg", "planning_failed"])
        self.assertEqual(events[-1][1]["reason"], "planner_exception")


if __name__ == "__main__":
    unittest.main()
