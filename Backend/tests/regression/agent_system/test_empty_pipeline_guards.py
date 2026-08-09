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
    async def test_rejects_session_without_plan(self):
        service = SessionService(_NoopWebSocketManager())
        service.store.put(Session(session_id="missing-plan"))

        with self.assertRaisesRegex(
            SessionExecutionConflict,
            "no confirmed executable pipeline plan",
        ):
            await service.start_execution("missing-plan", self._executor)

    async def test_rejects_empty_confirmed_plan(self):
        service = SessionService(_NoopWebSocketManager())
        session = Session(session_id="empty-plan")
        session.plan = StudyPlan(state="confirmed", steps=[])
        service.store.put(session)

        with self.assertRaisesRegex(
            SessionExecutionConflict,
            "no confirmed executable pipeline plan",
        ):
            await service.start_execution("empty-plan", self._executor)

    async def test_rejects_unconfirmed_plan(self):
        service = SessionService(_NoopWebSocketManager())
        session = Session(session_id="draft-plan")
        session.plan = StudyPlan(
            state="not_confirmed",
            steps=[
                StudyStep(
                    title="Cohort",
                    description="Select the study cohort",
                    module="study_population_planner_agent",
                )
            ],
        )
        service.store.put(session)

        with self.assertRaisesRegex(
            SessionExecutionConflict,
            "no confirmed executable pipeline plan",
        ):
            await service.start_execution("draft-plan", self._executor)

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
