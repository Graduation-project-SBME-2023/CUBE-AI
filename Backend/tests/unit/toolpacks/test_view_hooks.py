"""The on_result hooks (merged plan, Phase 2.4 step 5): golden tests against the
legacy monitor they were copied from, plus the agent-output read it got wrong."""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_system.toolkit.context import use_context
from agent_system.toolpacks.common import views
from tests.support.toolkit_fixtures import FakeEventSink, call_tool, make_context

pytestmark = pytest.mark.unit

pytest.importorskip("aiofiles")  # the legacy monitor's import; the project image has it
from agent_system.services import pipeline_monitor as legacy  # noqa: E402

SESSION = "11111111-1111-4111-8111-111111111111"
EMITTERS = {
    "emit_population_output": "population_output",
    "emit_segmentation_output": "segmentation_output",
    "emit_features_output": "features_output",
    "emit_modeling_output": "modeling_output",
    "emit_report_output": "report_output",
    "emit_latex_output": "latex_output",
}


@pytest.fixture
def legacy_views(monkeypatch):
    """Runs the legacy monitor's branch and records what it would have sent."""
    sent: list[tuple[str, dict]] = []
    for name, view_type in EMITTERS.items():
        async def emit(data, session_id=None, _type=view_type):
            sent.append((_type, data))

        monkeypatch.setattr(legacy, name, emit)

    async def no_recorded_output(session_id, agent_type):
        return {}

    monkeypatch.setattr(legacy, "_read_agent_output", no_recorded_output)

    def run(source: str, tool_name: str, content):
        sent.clear()
        asyncio.run(legacy._emit_tool_result_to_view(source, tool_name, content, SESSION, "/ws"))
        return list(sent)

    return run


def _ours(hook, agent: str, tool_name: str, content) -> list[tuple[str, dict]]:
    async def call():
        with use_context(make_context(session_id=SESSION, agent_type=agent, tool_name=tool_name)):
            return await hook(content, make_context(session_id=SESSION, agent_type=agent, tool_name=tool_name))

    return [(view.type, dict(view.payload)) for view in asyncio.run(call())]


PATIENT = {"data": {"patient_id": 1, "name": "x"}}
TARGETS = json.dumps({"status": "success", "cohort_id": "c1", "final_cohort_size": 3})
PDF = json.dumps({"status": "success", "report_id": "r1", "new_version": 2})

CASES = [
    # (hook, agent, tool, result)
    (views.on_population_tool, "study_population_planner_agent", "get_patient", PATIENT),
    (views.on_population_tool, "study_population_planner_agent", "get_volume", PATIENT),
    (views.on_population_tool, "study_population_planner_agent", "generate_targets", TARGETS),
    (views.on_population_tool, "segmentation_agent", "get_patient", PATIENT),  # no view
    (views.on_modeling_results, "result_reporter_agent", "get_modeling_results", "## Results"),
    (views.on_modeling_results, "latex_reporter_agent", "get_modeling_results", "## Results"),
    (views.on_latex_pdf, "latex_reporter_agent", "latex_to_pdf", PDF),
    (views.on_latex_pdf, "result_reporter_agent", "latex_to_pdf", PDF),  # no view
]


NO_VIEW = {
    ("segmentation_agent", "get_patient"),
    ("latex_reporter_agent", "get_modeling_results"),
    ("result_reporter_agent", "latex_to_pdf"),
}


@pytest.mark.parametrize(("hook", "agent", "tool_name", "result"), CASES)
def test_a_hook_sends_what_the_legacy_monitor_sent(legacy_views, hook, agent, tool_name, result):
    ours = _ours(hook, agent, tool_name, result)
    assert ours == legacy_views(agent, tool_name, result)
    # Guards the comparison itself: two empty lists would also be equal.
    assert bool(ours) == ((agent, tool_name) not in NO_VIEW)


@pytest.mark.parametrize(
    "agent",
    [
        "study_population_planner_agent",
        "segmentation_agent",
        "feature_extraction_agent",
        "modeling_agent",  # its summary is never a modeling view
        "result_reporter_agent",
        "latex_reporter_agent",
    ],
)
def test_write_agent_output_picks_the_legacy_view(legacy_views, fake_domain_api, agent):
    # No output recorded yet: both fall back to the tool's own result.
    fake_domain_api.get(f"/runs/session/{SESSION}").respond(json=[])
    result = json.dumps({"status": "ok", "agent_run_id": "r1"})

    ours = _ours(views.on_agent_output, agent, "write_agent_output", result)
    assert ours == legacy_views(agent, "write_agent_output", result)
    assert bool(ours) == (agent != "modeling_agent")


def test_the_recorded_output_is_read_by_the_node(fake_domain_api):
    """The legacy copy read `step.get("position")` (NameError since Phase 6.1)."""
    fake_domain_api.get(f"/runs/session/{SESSION}").respond(json=[
        {"run_id": "r0", "node": {"target_name": "other_agent"}},
        {
            "run_id": "r1",
            "node_id": "n1",
            "node": {"target_name": "segmentation_agent", "position": 2},
            "scratchpad_summary": {"agent_output": {
                "agent_role": "roi_segmentation", "status": "completed", "recorded_at": "t",
                "outputs": {"masks": 3},
            }},
            "scratchpad_warnings": {"items": [{"message": "w"}]},
        },
    ])

    async def read():
        with use_context(make_context(session_id=SESSION)):
            return await views.read_agent_output(SESSION, "segmentation_agent")

    recorded = asyncio.run(read())

    assert recorded["position"] == 2
    assert (recorded["run_id"], recorded["outputs"], recorded["warnings"]) == ("r1", {"masks": 3}, ["w"])


def test_dispatch_sends_the_hooks_views(fake_domain_api):
    """End to end: a tool's hook runs after the call and its view reaches the sink."""
    from agent_system.toolpacks.db_reads.patients.get_patient import get_patient

    fake_domain_api.get("/patients/1").respond(json={"patient_id": 1})
    sink = FakeEventSink()
    ctx = make_context(session_id=SESSION, agent_type="study_population_planner_agent", event_sink=sink)

    call_tool(get_patient, {"patient_id": 1}, ctx)

    [view] = sink.views
    assert view.type == "population_output"
    assert view.payload["tool"] == "get_patient"
    assert view.payload["data"] == {"data": {"patient_id": 1}}
