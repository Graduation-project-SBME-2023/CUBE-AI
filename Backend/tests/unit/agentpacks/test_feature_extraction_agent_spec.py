"""feature_extraction_agent, as loaded from agentpacks/ (merged plan, Phase 3.2)."""

from __future__ import annotations

import asyncio
import functools

import httpx
import pytest
from pydantic import ValidationError

from agent_system.toolkit.agents.engine import AgentResult, AgentStatus
from agent_system.toolkit.artifacts.builtin import FEATURE_IMPORTANCE, FEATURE_TABLE
from agent_system.toolkit.catalog import build
from tests.support.toolkit_fixtures import TEST_SESSION_ID, make_context

pytestmark = pytest.mark.unit

RUN_ID = "7d1f4c2e-0000-4000-8000-000000000003"
OTHER_RUN_ID = "7d1f4c2e-0000-4000-8000-000000000099"
TOP_10 = "3c9a0000-0000-4000-8000-000000000010"
ALL = "3c9a0000-0000-4000-8000-0000000000a1"
OUTPUT = {
    "feature_classes": ["firstorder", "shape", "glcm"],
    "radiomics_rows": 24,
    "feature_sets": [
        {"set_key": "top_10", "feature_set_id": TOP_10, "feature_count": 10, "subject_count": 24},
        {"set_key": "all_features", "feature_set_id": ALL, "feature_count": 107, "subject_count": 24},
    ],
    "selection_method": "rf",
    "top_features": ["original_glcm_Contrast", "original_shape_Sphericity"],
    "label_supervised_on_full_data": False,
}


@functools.cache
def _spec():
    return build().agents.get("feature_extraction_agent")


@pytest.fixture(scope="module")
def spec():
    return _spec()


def test_the_spec(spec):
    assert spec.kind == "execution"
    assert {name: port.artifact_type for name, port in spec.inputs.items()} == {
        "cohort": "Cohort",
        "masks": "MaskSet",
    }
    features, importance = spec.outputs["features"], spec.outputs["importance"]
    # Every frozen set feeds run_modeling's many input (Phase 7.3).
    assert (features.artifact_type, features.many, features.required) == ("FeatureTable", True, True)
    assert (importance.artifact_type, importance.many, importance.required) == (
        "FeatureImportance", False, False,
    )
    # The legacy agent had 35 iterations and browse_workspace to hunt for directories.
    assert spec.max_tool_iterations == 10
    assert "browse_workspace" not in spec.allowed_tools
    assert spec.timeout_s > 4 * 3600  # longer than radiomics_extract_features' own budget


@pytest.mark.parametrize(
    "change",
    [
        {"feature_sets": []},
        {"feature_classes": []},
        {"radiomics_rows": 0},
        {"feature_sets": [OUTPUT["feature_sets"][0], OUTPUT["feature_sets"][0]]},
        {"feature_sets": [{"set_key": "top_10", "feature_set_id": "top_10.csv",
                           "feature_count": 10, "subject_count": 24}]},
        {"csv_path": "features.csv"},
    ],
)  # fmt: skip
def test_the_output_contract_refuses(spec, change):
    with pytest.raises(ValidationError):
        spec.output_contract.model_validate(OUTPUT | change)


# --- the ports


def _result(output=OUTPUT) -> AgentResult:
    return AgentResult(status=AgentStatus.COMPLETED, output=output)


def test_one_feature_table_per_frozen_set(spec):
    artifacts = spec.outputs["features"].extract_artifacts(_result())

    assert [dict(a.ref) for a in artifacts] == [{"feature_set_id": TOP_10}, {"feature_set_id": ALL}]
    assert dict(artifacts[1].summary) == {"n_rows": 24, "n_features": 107}
    for artifact in artifacts:
        FEATURE_TABLE.validate_artifact(artifact)


def test_the_importance_ranks_all_features(spec):
    (artifact,) = spec.outputs["importance"].extract_artifacts(_result())

    assert dict(artifact.ref) == {"feature_set_id": ALL, "method": "rf"}
    assert list(artifact.summary["top_features"]) == OUTPUT["top_features"]
    FEATURE_IMPORTANCE.validate_artifact(artifact)


def test_no_importance_without_the_all_features_set(spec):
    output = OUTPUT | {"feature_sets": OUTPUT["feature_sets"][:1]}

    assert spec.outputs["importance"].extract_artifacts(_result(output)) == ()


# --- completion check


def _set(feature_set_id, set_key, features, run=RUN_ID, frozen=True):
    return {
        "feature_set_id": feature_set_id,
        "created_by_run_id": run,
        "set_key": set_key,
        "feature_count": features,
        "subject_count": 24,
        "frozen_at": "2026-10-10T10:00:00Z" if frozen else None,
    }


RECORDED = [_set(TOP_10, "top_10", 10), _set(ALL, "all_features", 107)]


def _check(spec, fake_domain_api, recorded=RECORDED, agent_status="completed", output=OUTPUT):
    fake_domain_api.get(f"/feature-sets/session/{TEST_SESSION_ID}").mock(
        return_value=httpx.Response(200, json=recorded)
    )
    stored = {"agent_output": {"status": agent_status}} if agent_status else {}
    fake_domain_api.get(f"/runs/{RUN_ID}").mock(
        return_value=httpx.Response(200, json={"run_id": RUN_ID, "scratchpad_summary": stored})
    )
    parsed = spec.output_contract.model_validate(output) if output is not None else None
    return asyncio.run(spec.completion_check(make_context(run_id=RUN_ID), parsed))


def test_complete_when_every_reported_set_is_recorded(spec, fake_domain_api):
    assert _check(spec, fake_domain_api) == []


@pytest.mark.parametrize(
    ("recorded", "expected"),
    [
        ([RECORDED[0]], ["is not a frozen set of this run"]),
        ([RECORDED[0], _set(ALL, "all_features", 107, run=OTHER_RUN_ID)], ["is not a frozen set of this run"]),
        ([RECORDED[0], _set(ALL, "all_features", 107, frozen=False)], ["is not a frozen set of this run"]),
        ([RECORDED[0], _set(ALL, "all_features", 95)], ["recorded as set_key=all_features, 95 features"]),
        ([*RECORDED, _set("3c9a0000-0000-4000-8000-000000000020", "top_20", 20)], ["missing: ['top_20']"]),
        ([], ["is not a frozen set", "is not a frozen set", "froze no feature set"]),
    ],
)  # fmt: skip
def test_what_is_wrong_is_named(spec, fake_domain_api, recorded, expected):
    issues = _check(spec, fake_domain_api, recorded)

    assert len(issues) == len(expected)
    for issue, text in zip(issues, expected):
        assert text in issue


def test_the_output_must_be_recorded(spec, fake_domain_api):
    (issue,) = _check(spec, fake_domain_api, agent_status=None)

    assert "write_agent_output" in issue


def test_without_a_structured_answer(spec, fake_domain_api):
    (issue,) = _check(spec, fake_domain_api, output=None)

    assert "structured format" in issue
