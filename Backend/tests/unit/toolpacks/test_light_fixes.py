"""The light-tool fixes of Appendix D (merged plan, Phase 2.4): L5-L12 and the
cohort gate S12, plus one lifecycle event per call."""

from __future__ import annotations

import asyncio
import json
import sys
import types

import httpx
import pytest
import respx

from agent_system.toolkit.context import use_context
from agent_system.toolpacks.bookkeeping import agent_output
from agent_system.toolpacks.bookkeeping.browse_workspace import browse_workspace
from agent_system.toolpacks.common import domain_api
from agent_system.toolpacks.population import cohort_labels, targets_matcher
from agent_system.toolpacks.population.validate_anatomy import (
    _resolve_anatomy_group,
    validate_population_anatomy,
)
from agent_system.toolpacks.reporting.get_modeling_results import get_modeling_results
from agent_system.toolpacks.segmentation import list_models as list_models_module
from agent_system.toolpacks.segmentation import run_segmentation
from tests.support.toolkit_fixtures import FakeEventSink, call_tool, make_context

pytestmark = pytest.mark.unit

SESSION = "11111111-1111-4111-8111-111111111111"
RUN = "22222222-2222-4222-8222-222222222222"
INFERENCE = "http://inference.test"


def _ctx(**fields):
    return make_context(session_id=SESSION, run_id=RUN, **fields)


# --- domain_api: every failure is a DomainApiError with a code


def test_an_unreachable_api_is_a_domain_error(fake_domain_api):
    fake_domain_api.get("/x").mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(domain_api.DomainApiError) as caught:
        asyncio.run(domain_api.request("GET", "/x"))

    assert caught.value.status_code is None
    assert caught.value.error_code == "unavailable"


@pytest.mark.parametrize(
    ("status", "code"), [(404, "not_found"), (409, "conflict"), (422, "bad_request"), (503, "upstream_error")]
)
def test_a_domain_error_maps_to_an_error_code(status, code):
    result = domain_api.error_result_from(domain_api.DomainApiError("x", status), "Saving")
    assert result == {"status": "error", "error_code": code, "error_message": "Saving: x"}


# --- L5: validate_population_anatomy


@pytest.mark.parametrize("region", ["", "  ", "a", "ab"])
def test_a_too_short_region_names_nothing(region):
    assert _resolve_anatomy_group(region) == (None, [])
    result = call_tool(
        validate_population_anatomy,
        {"hypothesis_body_region": region, "volume_details": []},
        _ctx(),
    )
    assert result["error_code"] == "invalid_args"


def test_a_real_region_still_resolves():
    assert _resolve_anatomy_group("kidney")[0] == "abdomen"
    assert _resolve_anatomy_group("abd")[0] == "abdomen"  # a keyword prefix of 3+ letters


def test_a_volume_without_uid_is_refused():
    result = call_tool(
        validate_population_anatomy,
        {"hypothesis_body_region": "lung", "volume_details": [{"series_description": "CT"}]},
        _ctx(),
    )
    assert result["error_code"] == "invalid_args"


def test_a_failed_cohort_write_is_an_error_result(fake_domain_api):
    fake_domain_api.get(
        f"/runs/session/{SESSION}/by-target/study_population_planner_agent"
    ).respond(json={"run": {"run_id": RUN}})
    fake_domain_api.put(f"/cohorts/run/{RUN}/population").mock(return_value=httpx.Response(503))

    result = call_tool(
        validate_population_anatomy,
        {"hypothesis_body_region": "lung", "volume_details": [{"uid": "v1"}]},
        make_context(session_id=SESSION),
    )

    assert result["error_code"] == "upstream_error"


# --- L6: extract_cohort_labels keeps every label that came back


def test_one_failed_llm_call_keeps_the_other_labels(monkeypatch):
    members = [
        {"volume_series_instance_uid": "v1", "subject_key": "p1"},
        {"volume_series_instance_uid": "v2", "subject_key": "p2"},
    ]
    written = {}

    async def fake_cohort():
        return {"cohort_id": "c1", "criteria": {}, "members": members}

    async def fake_request(method, path, **kwargs):
        return {"orthanc_patient_id": path.rsplit("/", 1)[1]}

    async def fake_write(cohort_id, labels):
        written["labels"] = labels

    async def fake_process(client, model, uid, text, prompt, sem):
        if uid == "v2":
            raise ConnectionError("LLM down")
        return {"volume_uid": uid, "has_target_disease": True, "confidence": "high", "source_evidence": "e"}

    monkeypatch.setattr(cohort_labels.domain_api, "require_population_cohort", fake_cohort)
    monkeypatch.setattr(cohort_labels.domain_api, "request", fake_request)
    monkeypatch.setattr(cohort_labels.domain_api, "write_cohort_labels", fake_write)
    monkeypatch.setattr(cohort_labels, "_process_patient_record", fake_process)
    monkeypatch.setattr(
        cohort_labels, "_load_clinical_records", lambda _: ([("p1", {"r": 1}), ("p2", {"r": 2})], "x.json")
    )
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=lambda **_: object()))

    result = call_tool(cohort_labels.extract_cohort_labels, {}, _ctx())

    assert result["status"] == "success"
    assert result["llm_unavailable"] is True
    by_uid = {label["volume_series_instance_uid"]: label for label in written["labels"]}
    assert by_uid["v1"]["has_target_disease"] is True  # kept, not dropped with v2
    assert by_uid["v2"]["label_provenance"]["reason"] == "llm_unavailable"


# --- L7 + S12: generate_targets


def _cohorts(*outcomes):
    queue = list(outcomes)

    async def get_population_cohort(for_session=None):
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return get_population_cohort


def test_a_transient_error_does_not_end_the_cohort_wait(monkeypatch):
    monkeypatch.setenv("COHORT_APPROVAL_POLL_SECONDS", "0.01")
    frozen = {"cohort_id": "c1", "frozen_at": "t"}
    monkeypatch.setattr(
        targets_matcher.domain_api,
        "get_population_cohort",
        _cohorts({"cohort_id": "c1"}, domain_api.DomainApiError("down", 503), frozen),
    )
    sink = FakeEventSink()

    async def wait():
        with use_context(_ctx(tool_name="generate_targets", event_sink=sink)):
            return await targets_matcher._wait_for_cohort_approval(timeout_seconds=5)

    assert asyncio.run(wait()) == frozen
    assert [view.type for view in sink.views] == ["agent_waiting_for_user_input"]


def test_the_waiting_view_is_announced_once_per_session(monkeypatch):
    monkeypatch.setenv("COHORT_APPROVAL_POLL_SECONDS", "0.01")
    monkeypatch.setattr(targets_matcher.domain_api, "get_population_cohort", _cohorts({"cohort_id": "c1"}))
    sinks = {"a": FakeEventSink(), "b": FakeEventSink()}

    async def wait(session):
        with use_context(make_context(session_id=session, tool_name="t", event_sink=sinks[session])):
            await targets_matcher._wait_for_cohort_approval(timeout_seconds=0.05)

    async def both():
        return await asyncio.gather(wait("a"), wait("b"), return_exceptions=True)

    asyncio.run(both())
    # One session's wait never hides the other's announcement (it was one global flag).
    assert len(sinks["a"].views) == len(sinks["b"].views) == 1


def test_a_cohort_wait_that_times_out_is_an_error_result(monkeypatch):
    cohort = {"cohort_id": "c1", "members": [{"volume_series_instance_uid": "v1", "label": {"has_target_disease": True}}]}
    monkeypatch.setattr(targets_matcher.domain_api, "get_population_cohort", _cohorts(cohort))

    async def never_approved():
        raise targets_matcher.PollTimeout("no result")

    monkeypatch.setattr(targets_matcher, "_wait_for_cohort_approval", never_approved)

    result = call_tool(targets_matcher.generate_targets, {}, _ctx())

    assert result["error_code"] == "timeout"


# --- L8: run_segmentation's settings


@pytest.mark.parametrize("raw", ["abc", "-5"])
def test_a_bad_segmentation_setting_falls_back(monkeypatch, raw):
    monkeypatch.setenv("SEGMENTATION_JOB_TIMEOUT_SECONDS", raw)
    monkeypatch.setenv("SEGMENTATION_POLL_SECONDS", raw)
    assert run_segmentation._job_timeout_s() == 1800.0
    assert run_segmentation._poll_s() == 3.0


# --- L9: browse_workspace


def test_a_missing_directory_is_an_error_result(tmp_path):
    result = call_tool(
        browse_workspace, {"params": {"directory": "nope"}}, _ctx(workspace_dir=tmp_path)
    )
    assert result["error_code"] == "not_found"


# --- L10: get_modeling_results


def test_unreadable_modeling_results_are_an_error_result(fake_domain_api):
    fake_domain_api.get(f"/feature-sets/session/{SESSION}").mock(return_value=httpx.Response(500))

    result = call_tool(get_modeling_results, {}, _ctx())

    assert result["error_code"] == "upstream_error"


# --- L11: list_models returns one type


def test_list_models_returns_the_list(monkeypatch):
    monkeypatch.setenv("INFERENCE_API_URL", INFERENCE)
    monkeypatch.setattr(list_models_module, "get_enabled_segmentation_model_paths", lambda: {"spleen": None})

    with respx.mock(base_url=INFERENCE) as router:
        router.get("/models/").respond(json=[{"name": "spleen", "type": "automatic"}, {"name": "off"}])
        result = call_tool(list_models_module.list_models, {}, _ctx())

    assert isinstance(result, list)
    assert [model["name"] for model in result] == ["spleen"]


def test_list_models_failure_is_an_error_result(monkeypatch):
    monkeypatch.setenv("INFERENCE_API_URL", INFERENCE)

    with respx.mock(base_url=INFERENCE) as router:
        router.get("/models/").mock(return_value=httpx.Response(502))
        result = call_tool(list_models_module.list_models, {}, _ctx())

    assert result["error_code"] == "unavailable"


# --- L12: write_agent_output cleans up off the event loop


def test_the_temp_cleanup_runs_in_a_thread(monkeypatch, fake_domain_api):
    fake_domain_api.put(f"/runs/{RUN}/output").respond(json={"run_id": RUN, "node_id": "n1"})
    threaded = []

    async def fake_to_thread(fn, *args):
        threaded.append(fn.__name__)
        return fn(*args)

    monkeypatch.setattr(agent_output.asyncio, "to_thread", fake_to_thread)
    params = {"agent_id": "a", "agent_role": "r", "status": "completed"}

    result = json.loads(call_tool(agent_output.write_agent_output, {"params": params}, _ctx()))

    assert result["status"] == "ok"
    assert threaded == ["_cleanup_run_temp_dirs"]


# --- one lifecycle event per call


def test_a_call_has_one_start_and_one_finish(monkeypatch):
    monkeypatch.setenv("INFERENCE_API_URL", INFERENCE)
    monkeypatch.setattr(list_models_module, "get_enabled_segmentation_model_paths", lambda: {})

    sink = FakeEventSink()
    with respx.mock(base_url=INFERENCE) as router:
        router.get("/models/").respond(json=[])
        call_tool(list_models_module.list_models, {}, _ctx(event_sink=sink))

    async def drain():  # the tool's own logs are scheduled, not awaited
        await asyncio.sleep(0)

    asyncio.run(drain())
    assert sink.types.count("tool_started") == 1
    assert sink.types.count("tool_finished") == 1
