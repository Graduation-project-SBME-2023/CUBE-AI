"""run_segmentation_with_guard (merged plan, Phase 2.4 step 3, L13): one name,
critical, identity from the context, the output recorded on the run."""

from __future__ import annotations

import httpx
import pytest

from agent_system.toolpacks.segmentation import run_with_guard
from agent_system.toolpacks.segmentation.run_with_guard import run_segmentation_with_guard
from agent_system.toolkit.tools.registry import spec_of
from tests.support.toolkit_fixtures import call_tool, make_context

pytestmark = pytest.mark.unit

SESSION = "11111111-1111-4111-8111-111111111111"
RUN = "22222222-2222-4222-8222-222222222222"
ATTEMPT = "33333333-3333-4333-8333-333333333333"


class FakeModule:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls: list[str] = []
        self.workspace = None

    async def initialize(self):
        self.calls.append("initialize")

    async def run(self, workspace):
        self.calls.append("run")
        self.workspace = workspace
        if self.error:
            raise self.error
        return self.result

    async def close(self):
        self.calls.append("close")


@pytest.fixture
def module(monkeypatch):
    fake = FakeModule(
        {"status": "completed", "pair_count": 2, "accepted_count": 2, "segmentation_batch_id": "b1"}
    )
    built = {}

    def factory(session_id, run_id):
        built.update(session_id=session_id, run_id=run_id)
        return fake

    monkeypatch.setattr(run_with_guard, "_segmentation_module", factory)
    fake.built = built
    return fake


@pytest.fixture
def ctx(tmp_path):
    return make_context(
        session_id=SESSION,
        run_id=RUN,
        attempt_id=ATTEMPT,
        agent_type="segmentation_agent",
        workspace_dir=tmp_path,
    )


def test_one_name_critical_and_no_identity_arguments():
    spec = spec_of(run_segmentation_with_guard)
    assert spec.name == "run_segmentation_with_guard"
    assert spec.critical is True
    # Session, run and workspace come from the context, never from the model;
    # the one argument receives the Cohort input port.
    assert spec.arg_names == {"cohort_id"}
    assert spec.inputs["cohort"].param == "cohort_id"


def test_the_mask_set_port_reads_the_result():
    spec = spec_of(run_segmentation_with_guard)
    [artifact] = spec.outputs["masks"].extract_artifacts(
        {"segmentation_batch_id": "b1", "accepted_count": 3, "ready_count": 3, "failures": [{}]}
    )
    assert dict(artifact.ref) == {"segmentation_batch_id": "b1"}
    assert dict(artifact.summary) == {"n_masks": 3, "n_failed": 1, "review_status": "submitted"}


def test_another_cohort_is_refused(module, ctx, fake_domain_api):
    fake_domain_api.get(f"/cohorts/session/{SESSION}/population").respond(json={"cohort_id": "c1"})

    result = call_tool(run_segmentation_with_guard, {"cohort_id": "c2"}, ctx)

    assert result["error_code"] == "invalid_args"
    assert module.calls == []


def test_it_segments_the_contexts_session_and_records_the_output(module, ctx, fake_domain_api):
    route = fake_domain_api.put(f"/runs/{RUN}/output").respond(json={"run_id": RUN})

    result = call_tool(run_segmentation_with_guard, {}, ctx)

    assert result["status"] == "completed"
    assert module.built == {"session_id": SESSION, "run_id": RUN}
    assert module.workspace == str(ctx.workspace_dir)
    assert module.calls == ["initialize", "run", "close"]
    request = route.calls.last.request
    assert request.headers["X-Attempt-Id"] == ATTEMPT  # D24
    recorded = httpx.Response(200, content=request.content).json()
    assert recorded["status"] == "completed"
    assert recorded["output_files"]["segmentation_batch_id"] == "b1"
    assert recorded["summary"] == "Segmentation completed with 2/2 accepted segmentation masks."


def test_a_failed_segmentation_is_an_error_after_its_output_is_recorded(module, ctx, fake_domain_api):
    module.result = {"status": "failed", "pair_count": 2, "accepted_count": 0, "failures": ["x"]}
    route = fake_domain_api.put(f"/runs/{RUN}/output").respond(json={})

    result = call_tool(run_segmentation_with_guard, {}, ctx)

    assert result["error_code"] == "segmentation_failed"
    assert result["segmentation"]["failures"] == ["x"]
    assert route.called  # recorded first, as the AutoGen tool did


def test_a_partial_segmentation_is_still_a_success(module, ctx, fake_domain_api):
    module.result = {"status": "partial", "pair_count": 2, "accepted_count": 1, "failures": ["x"]}
    fake_domain_api.put(f"/runs/{RUN}/output").respond(json={})

    assert call_tool(run_segmentation_with_guard, {}, ctx)["status"] == "partial"


def test_the_module_is_closed_when_it_fails(module, ctx):
    module.error = RuntimeError("MONAI down")

    result = call_tool(run_segmentation_with_guard, {}, ctx)

    assert result["status"] == "error"  # dispatch turns the exception into an error result
    assert module.calls == ["initialize", "run", "close"]


def test_an_unrecorded_output_is_an_error(module, ctx, fake_domain_api):
    fake_domain_api.put(f"/runs/{RUN}/output").mock(return_value=httpx.Response(503))

    assert call_tool(run_segmentation_with_guard, {}, ctx)["error_code"] == "upstream_error"


def test_outside_a_run_it_does_not_start(module, tmp_path):
    result = call_tool(
        run_segmentation_with_guard, {}, make_context(session_id=SESSION, workspace_dir=tmp_path)
    )

    assert result["error_code"] == "no_run"
    assert module.calls == []
