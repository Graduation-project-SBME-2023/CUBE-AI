"""Tools take their identity from the ToolContext, never from the environment
(merged plan, Phase 2.3)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_system.toolkit.context import use_context
from agent_system.toolpacks.bookkeeping.browse_workspace import browse_workspace
from agent_system.toolpacks.bookkeeping.scratchpad import scratchpad_write_note
from agent_system.toolpacks.common import domain_api, llm, settings
from agent_system.toolpacks.population.inclusion_criteria import write_inclusion_criteria
from tests.support.toolkit_fixtures import FakeEventSink, call_tool, make_context

pytestmark = pytest.mark.unit

TOOLPACKS_ROOT = Path(__file__).resolve().parents[3] / "agent_system" / "toolpacks"
IDENTITY = ("SESSION_ID", "RUN_ID", "AGENT_ID", "NODE_ID", "WORKSPACE_DIR")
LLM_ENV = ("MODEL_BASE_URL", "MODEL_NAME", "MODEL_API_KEY")

SESSION = "11111111-1111-4111-8111-111111111111"
RUN = "22222222-2222-4222-8222-222222222222"
ATTEMPT = "33333333-3333-4333-8333-333333333333"


@pytest.fixture
def env_says_otherwise(monkeypatch):
    """Identity env vars that disagree with the context: a tool that still
    read them would hit the wrong run, session or workspace."""
    for name in IDENTITY:
        monkeypatch.setenv(name, "from-env")


def test_no_tool_reads_identity_from_the_environment():
    names = "|".join(IDENTITY + LLM_ENV)
    env_read = re.compile(rf"os\.(?:environ|getenv)\b[^\n]*[\"'](?:{names})[\"']")
    constant = re.compile(rf"^(?:{names})\s*=", re.M)
    offenders = [
        f"{path.relative_to(TOOLPACKS_ROOT)}: {match.group(0)}"
        for path in TOOLPACKS_ROOT.rglob("*.py")
        for pattern in (env_read, constant)
        for match in pattern.finditer(path.read_text(encoding="utf-8"))
    ]
    assert not offenders


def test_the_node_and_agent_come_from_the_context(env_says_otherwise):
    with use_context(make_context(node_id="n1", agent_type="segmentation_agent")):
        assert domain_api.node_id() == "n1"
        assert domain_api.agent_type() == "segmentation_agent"
    assert domain_api.node_id() == domain_api.agent_type() == ""


def test_a_scratchpad_note_goes_to_the_contexts_run(env_says_otherwise, fake_domain_api):
    route = fake_domain_api.post(f"/runs/{RUN}/scratchpad/notes").respond(
        json={"scratchpad_notes": {"notes": [{"created_at": "t"}], "role": "r"}}
    )
    sink = FakeEventSink()
    ctx = make_context(
        session_id=SESSION,
        run_id=RUN,
        attempt_id=ATTEMPT,
        node_id="n1",
        agent_type="segmentation_agent",
        event_sink=sink,
    )

    result = json.loads(call_tool(scratchpad_write_note, {"content": "hello"}, ctx))

    assert result == {"status": "ok", "note_index": 0}
    assert route.calls.last.request.headers["X-Attempt-Id"] == ATTEMPT  # D24
    [view] = sink.views
    assert view.type == "scratchpad_update"
    assert view.payload["agent_id"] == "segmentation_agent"
    assert view.payload["run_id"] == RUN


def test_a_population_view_goes_to_the_contexts_session(env_says_otherwise, fake_domain_api):
    fake_domain_api.get(
        f"/runs/session/{SESSION}/by-target/study_population_planner_agent"
    ).respond(json={"run": {"run_id": RUN}})
    fake_domain_api.put(f"/cohorts/run/{RUN}/population").respond(json={"cohort_id": "c1"})
    sink = FakeEventSink()
    ctx = make_context(session_id=SESSION, event_sink=sink)

    result = call_tool(
        write_inclusion_criteria,
        {"inclusion_criteria": ["a"], "exclusion_criteria": [], "modality": "CT"},
        ctx,
    )

    assert result["status"] == "success"
    [view] = sink.views
    assert view.type == "population_output"


def test_browse_workspace_defaults_to_the_contexts_workspace(env_says_otherwise, tmp_path):
    (tmp_path / "a.csv").write_text("x", encoding="utf-8")
    ctx = make_context(session_id=SESSION, workspace_dir=tmp_path)

    result = json.loads(call_tool(browse_workspace, {"params": {}}, ctx))

    assert result["status"] == "ok"
    assert [entry["path"] for entry in result["files"]] == ["a.csv"]


# --- the extraction LLM


class _Config:
    def __init__(self, **fields):
        self.__dict__.update({"base_url": None, "api_key": None, "model": "m"} | fields)


def test_the_extraction_llm_follows_the_client_configuration(monkeypatch):
    monkeypatch.setenv("MODEL_BASE_URL", "http://ignored")  # the old MCP env is not read
    monkeypatch.setattr(
        llm.client_config,
        "get_client_config",
        lambda: _Config(base_url="http://llm:1/v1", api_key="k", model="gpt-x"),
    )
    assert llm.extraction_endpoint() == llm.LlmEndpoint("http://llm:1/v1", "gpt-x", "k")


def test_the_extraction_llm_defaults_to_local_ollama(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setattr(llm.client_config, "get_client_config", lambda: _Config())
    endpoint = llm.extraction_endpoint()
    assert endpoint.base_url == llm.DEFAULT_OLLAMA_BASE_URL
    assert endpoint.api_key == llm.DEFAULT_API_KEY


@pytest.mark.parametrize(("raw", "expected"), [("8", 8), ("0.4", 1), ("-2", 5), ("x", 5)])
def test_the_llm_concurrency_is_at_least_one(monkeypatch, raw, expected):
    monkeypatch.setenv("LLM_CONCURRENCY_LIMIT", raw)
    assert settings.llm_concurrency_limit() == expected

