import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2]
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


def _write_ready_handoff(workspace: Path) -> None:
    (workspace / "populations" / "targets").mkdir(parents=True)
    (workspace / "outputs").mkdir()
    (workspace / "config.json").write_text(
        json.dumps(
            {
                "anatomy_validated": True,
                "cohort_approved": True,
                "datasetlist": ["volume-1"],
            }
        ),
        encoding="utf-8",
    )
    (workspace / "populations" / "targets" / "targets.csv").write_text(
        "id,name,target\nvolume-1,patient-1,1\n",
        encoding="utf-8",
    )
    (workspace / "scratchpad.json").write_text(
        json.dumps(
            {
                "agents": {
                    POPULATION_AGENT_ID: {
                        "status": "done",
                        "summary": {"content": "Doctor approved one labeled volume."},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (workspace / "outputs" / f"{POPULATION_AGENT_ID}_output.json").write_text(
        json.dumps({"agent_id": POPULATION_AGENT_ID, "status": "completed"}),
        encoding="utf-8",
    )


class PopulationHandoffContractTests(unittest.TestCase):
    def test_rejects_candidate_only_config_from_failed_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "config.json").write_text(
                json.dumps(
                    {
                        "candidate_datasetlist": ["candidate-uid"],
                        "anatomy_validated": False,
                    }
                ),
                encoding="utf-8",
            )

            issues = population_handoff_issues(workspace)

            self.assertTrue(any("validate_population_anatomy" in issue for issue in issues))
            self.assertTrue(any("validated datasetlist" in issue for issue in issues))
            self.assertTrue(any("generate_targets" in issue for issue in issues))
            with self.assertRaisesRegex(RuntimeError, "Population handoff is incomplete"):
                require_population_handoff(workspace)

    def test_rejects_approved_but_empty_cohort(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_ready_handoff(workspace)
            config_path = workspace / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["datasetlist"] = []
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (workspace / "populations" / "targets" / "targets.csv").write_text(
                "id,name,target\n",
                encoding="utf-8",
            )

            issues = population_handoff_issues(workspace)

            self.assertTrue(any("datasetlist is empty" in issue for issue in issues))
            self.assertTrue(any("no cohort rows" in issue for issue in issues))

    def test_accepts_nonempty_doctor_approved_cohort(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_ready_handoff(workspace)

            self.assertEqual(population_handoff_issues(workspace), [])
            require_population_handoff(workspace)


class PopulationAgentRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_final_response_is_suppressed_and_retried(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "config.json").write_text(
                json.dumps({"candidate_datasetlist": ["candidate-uid"]}),
                encoding="utf-8",
            )
            calls = []

            async def fake_parent_stream(_agent, messages, _token):
                calls.append(messages)
                if len(calls) == 2:
                    _write_ready_handoff(workspace)
                yield Response(
                    chat_message=TextMessage(
                        source=POPULATION_AGENT_ID,
                        content="" if len(calls) == 1 else "Population complete",
                    )
                )

            agent = PopulationPlannerAgent.__new__(PopulationPlannerAgent)
            agent._workspace_folder = str(workspace)
            agent._population_completion_retries = 2

            with patch.object(AssistantAgent, "on_messages_stream", fake_parent_stream):
                events = [
                    event
                    async for event in agent.on_messages_stream(
                        [TextMessage(source="user", content="Run population selection")],
                        CancellationToken(),
                    )
                ]

            self.assertEqual(len(calls), 2)
            self.assertEqual(len(events), 1)
            self.assertIsInstance(events[0], Response)
            self.assertEqual(events[0].chat_message.content, "Population complete")
            self.assertIn("validate_population_anatomy", calls[1][0].content)
            self.assertIn("at least one volume", calls[1][0].content)

    async def test_repeated_incomplete_responses_fail_before_graph_handoff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = 0

            async def fake_parent_stream(_agent, _messages, _token):
                nonlocal calls
                calls += 1
                yield Response(
                    chat_message=TextMessage(source=POPULATION_AGENT_ID, content="")
                )

            agent = PopulationPlannerAgent.__new__(PopulationPlannerAgent)
            agent._workspace_folder = temp_dir
            agent._population_completion_retries = 1

            with patch.object(AssistantAgent, "on_messages_stream", fake_parent_stream):
                with self.assertRaisesRegex(RuntimeError, "mandatory handoff"):
                    async for _event in agent.on_messages_stream(
                        [TextMessage(source="user", content="Run population selection")],
                        CancellationToken(),
                    ):
                        pass

            self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
