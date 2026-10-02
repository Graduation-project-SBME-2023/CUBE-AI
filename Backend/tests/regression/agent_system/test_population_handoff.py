"""The population stage's durable completion contract."""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND_DIR = Path(__file__).resolve().parents[3]
AGENT_SYSTEM_DIR = BACKEND_DIR / "agent_system"
for import_root in (str(BACKEND_DIR), str(AGENT_SYSTEM_DIR)):
    if import_root not in sys.path:
        sys.path.insert(0, import_root)

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken

from agents.execution_agents.population_agent import PopulationPlannerAgent
from services.population_handoff import (
    POPULATION_AGENT_ID,
    population_handoff_issues,
    require_population_handoff,
)

SESSION_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"


def _ready_cohort(**overrides):
    cohort = {
        "cohort_id": "33333333-3333-4333-8333-333333333333",
        "frozen_at": "2026-09-15T10:00:00+00:00",
        "criteria": {"schema_version": 1, "labelling_mode": "automatic"},
        "label_definition": {"schema_version": 1, "kind": "binary", "field": "has_target_disease"},
        "selection_summary": {
            "schema_version": 1,
            "anatomy_validation": {"anatomy_validated": True},
        },
        "members": [
            {
                "volume_series_instance_uid": "volume-1",
                "subject_key": "patient-1",
                "ordinal": 1,
                "label": {"has_target_disease": True},
                "label_source": "manual_override",
            }
        ],
    }
    cohort.update(overrides)
    return cohort


def _ready_run(**overrides):
    run = {
        "run_id": RUN_ID,
        "status": "running",
        "scratchpad_notes": {"status": "done", "role": "population"},
        "scratchpad_summary": {
            "scratchpad_summary": {"content": "Doctor approved one labeled volume."},
            "agent_output": {"status": "completed", "agent_role": "population"},
        },
    }
    run.update(overrides)
    return run


def _fetchers(cohort=None, run=None):
    return {
        "fetch_cohort": AsyncMock(return_value=cohort),
        "fetch_run": AsyncMock(return_value=run),
    }


class PopulationHandoffContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_complete_handoff_has_no_issues(self):
        issues = await population_handoff_issues(
            SESSION_ID, RUN_ID, **_fetchers(_ready_cohort(), _ready_run())
        )
        self.assertEqual(issues, [])
        await require_population_handoff(
            SESSION_ID, RUN_ID, **_fetchers(_ready_cohort(), _ready_run())
        )

    async def test_every_missing_record_is_reported(self):
        cases = {
            "the population cohort has not been recorded": (None, _ready_run()),
            "validate_population_anatomy has not completed": (
                _ready_cohort(selection_summary={"schema_version": 1}),
                _ready_run(),
            ),
            "validated cohort is empty": (_ready_cohort(members=[]), _ready_run()),
            "doctor cohort approval has not completed": (
                _ready_cohort(frozen_at=None),
                _ready_run(),
            ),
            "population scratchpad status is not done": (
                _ready_cohort(),
                _ready_run(scratchpad_notes={"status": "in_progress"}),
            ),
            "population scratchpad summary is missing": (
                _ready_cohort(),
                _ready_run(scratchpad_summary={"agent_output": {"status": "completed"}}),
            ),
            "population structured output status is not completed": (
                _ready_cohort(),
                _ready_run(
                    scratchpad_summary={
                        "scratchpad_summary": {"content": "done"},
                        "agent_output": {"status": "partial"},
                    }
                ),
            ),
        }
        for expected, (cohort, run) in cases.items():
            with self.subTest(expected):
                issues = await population_handoff_issues(
                    SESSION_ID, RUN_ID, **_fetchers(cohort, run)
                )
                self.assertTrue(
                    any(expected in issue for issue in issues),
                    f"{expected!r} not in {issues}",
                )
                with self.assertRaises(RuntimeError):
                    await require_population_handoff(
                        SESSION_ID, RUN_ID, **_fetchers(cohort, run)
                    )

    async def test_an_approved_cohort_with_no_labelled_volume_is_not_a_handoff(self):
        cohort = _ready_cohort(
            members=[
                {
                    "volume_series_instance_uid": "volume-1",
                    "subject_key": "patient-1",
                    "ordinal": 1,
                    "label": None,
                    "label_source": "pending",
                }
            ]
        )
        issues = await population_handoff_issues(
            SESSION_ID, RUN_ID, **_fetchers(cohort, _ready_run())
        )
        self.assertIn("the approved cohort has no labelled volume", issues)

    async def test_a_step_with_no_run_can_verify_nothing(self):
        issues = await population_handoff_issues(
            SESSION_ID, None, **_fetchers(_ready_cohort(), _ready_run())
        )
        self.assertEqual(issues, ["the population step has no agent run; nothing can be verified"])


class PopulationPlannerCompletionTests(unittest.IsolatedAsyncioTestCase):
    """The planner may not finish before its handoff is durable."""

    async def test_incomplete_final_response_is_suppressed_and_retried(self):
        calls = []
        state = {"complete": False}

        async def fake_parent_stream(_agent, messages, _token):
            calls.append(messages)
            if len(calls) == 2:
                state["complete"] = True
            yield Response(
                chat_message=TextMessage(
                    source=POPULATION_AGENT_ID,
                    content="" if len(calls) == 1 else "Population complete",
                )
            )

        async def fake_issues(session_id, run_id, **kwargs):
            assert (session_id, run_id) == (SESSION_ID, RUN_ID)
            return [] if state["complete"] else ["doctor cohort approval has not completed"]

        agent = PopulationPlannerAgent.__new__(PopulationPlannerAgent)
        agent._workspace_folder = "/workspace"
        agent._session_id = SESSION_ID
        agent._run_id = RUN_ID
        agent._population_completion_retries = 2

        with patch.object(AssistantAgent, "on_messages_stream", fake_parent_stream), patch(
            "agents.execution_agents.population_agent.population_handoff_issues", fake_issues
        ):
            events = [
                event
                async for event in agent.on_messages_stream(
                    [TextMessage(source="user", content="Run population selection")],
                    CancellationToken(),
                )
            ]

        self.assertEqual(len(calls), 2)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].chat_message.content, "Population complete")
        self.assertIn("validate_population_anatomy", calls[1][0].content)
        self.assertIn("at least one volume", calls[1][0].content)

    async def test_repeated_incomplete_responses_fail_before_graph_handoff(self):
        calls = 0

        async def fake_parent_stream(_agent, _messages, _token):
            nonlocal calls
            calls += 1
            yield Response(chat_message=TextMessage(source=POPULATION_AGENT_ID, content=""))

        async def always_incomplete(session_id, run_id, **kwargs):
            return ["doctor cohort approval has not completed"]

        agent = PopulationPlannerAgent.__new__(PopulationPlannerAgent)
        agent._workspace_folder = "/workspace"
        agent._session_id = SESSION_ID
        agent._run_id = RUN_ID
        agent._population_completion_retries = 1

        with patch.object(AssistantAgent, "on_messages_stream", fake_parent_stream), patch(
            "agents.execution_agents.population_agent.population_handoff_issues",
            always_incomplete,
        ):
            with self.assertRaisesRegex(RuntimeError, "mandatory"):
                async for _event in agent.on_messages_stream(
                    [TextMessage(source="user", content="Run population selection")],
                    CancellationToken(),
                ):
                    pass

        self.assertEqual(calls, 2)

    async def test_a_planner_with_no_session_is_the_local_harness_only(self):
        """Production always binds a session; the legacy local entrypoint has
        no durable records to check and must stay usable."""

        async def fake_parent_stream(_agent, _messages, _token):
            yield Response(chat_message=TextMessage(source=POPULATION_AGENT_ID, content="ok"))

        agent = PopulationPlannerAgent.__new__(PopulationPlannerAgent)
        agent._workspace_folder = None
        agent._session_id = None
        agent._run_id = None
        agent._population_completion_retries = 0

        with patch.object(AssistantAgent, "on_messages_stream", fake_parent_stream):
            events = [
                event
                async for event in agent.on_messages_stream([], CancellationToken())
            ]
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
