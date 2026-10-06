"""The Definition of Ready of every light tool (merged plan, Phase 2.5).

Every light tool is called through dispatch with a fake context and a mocked
Application API: once on a path that succeeds, once on a path that fails. Every
call also checks that

- an error is an ``error_result`` (and a success never looks like an error);
- no identity environment variable is read (``os.environ`` is replaced by a
  mapping that records every read of one);

and the catalog is checked for the manifest items of the Definition of Ready.
A few tools have their success and error tests in their own file
(``COVERED_ELSEWHERE``); this module checks that those files exist.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_system.toolkit import catalog as catalog_module
from agent_system.toolkit.manifest import Execution, IsolationLevel, TrustLevel
from agent_system.toolkit.results import is_error_result
from agent_system.toolpacks.reporting import template_tools
from tests.support.toolkit_fixtures import call_tool, make_context

pytestmark = pytest.mark.unit

SESSION = "11111111-1111-4111-8111-111111111111"
RUN = "22222222-2222-4222-8222-222222222222"
ATTEMPT = "33333333-3333-4333-8333-333333333333"
POPULATION = "study_population_planner_agent"
IDENTITY_ENV = frozenset({"SESSION_ID", "RUN_ID", "AGENT_ID", "NODE_ID", "ATTEMPT_ID", "WORKSPACE_DIR"})

# Plan 7 moves these to jobs; they are not light tools.
HEAVY = {
    "radiomics_extract_features", "radiomics_analyze_importance", "run_segmentation",
    "run_segmentation_with_guard", "train_classifier", "run_classifier_inference",
    "train_regressor", "run_regression_inference", "refit_model",
}
TESTS = Path(__file__).resolve().parents[1]
# A light tool whose success and error tests live with the tool's own tests.
COVERED_ELSEWHERE = {
    "search_pubmed": "test_literature_tools.py",
    "search_arxiv": "test_literature_tools.py",
    "search_semantic_scholar": "test_literature_tools.py",
    "search_europe_pmc": "test_literature_tools.py",
    "score_paper_relevance": "test_literature_tools.py",
    "save_literature_output": "test_literature_tools.py",
    "latex_to_pdf": "test_latex_to_pdf.py",
    "list_models": "test_light_fixes.py",
    "extract_cohort_labels": "test_light_fixes.py",  # its success; the error is below
}

RUN_ROW = {"run_id": RUN, "node_id": "n1", "status": "running",
           "node": {"target_name": POPULATION, "position": 1}}
COHORT = {"cohort_id": "c1", "criteria": {}, "frozen_at": "t",
          "members": [{"volume_series_instance_uid": "v1", "subject_key": "p1",
                       "label": {"has_target_disease": True}}]}
GET, POST, PUT = "GET", "POST", "PUT"


@dataclass
class Case:
    args: dict[str, Any] = field(default_factory=dict)
    routes: list[tuple[str, str, Any]] = field(default_factory=list)  # (method, path, json)
    api_down: bool = False  # every Application API request gets a 503
    error_code: str | None = None  # None: a success
    setup: Callable[[pytest.MonkeyPatch, Path], None] | None = None


def _population(*extra):
    """The population run resolution, then ``extra`` routes."""
    return [(GET, f"/runs/{RUN}", RUN_ROW), *extra]


SUCCESS: dict[str, Case] = {
    # bookkeeping
    "write_agent_output": Case(
        {"params": {"agent_id": "a", "agent_role": "r", "status": "completed"}},
        [(PUT, f"/runs/{RUN}/output", {"run_id": RUN, "node_id": "n1"}),
         (GET, f"/runs/session/{SESSION}", [])],
    ),
    "read_agent_output": Case(
        {"params": {"agent_id": "segmentation_agent"}},
        [(GET, f"/runs/session/{SESSION}/by-target/segmentation_agent", {"run": RUN_ROW})],
    ),
    "read_all_agent_outputs": Case({}, [(GET, f"/runs/session/{SESSION}", [RUN_ROW])]),
    "browse_workspace": Case({"params": {}}),
    "read_agent_task_context": Case(
        {}, [(GET, f"/sessions/{SESSION}", {"prompt": "p"}), (GET, f"/runs/{RUN}", RUN_ROW)]
    ),
    "scratchpad_register_agent": Case(
        {"role": "r"}, [(POST, f"/runs/{RUN}/scratchpad/register", {"scratchpad_notes": {}})]
    ),
    "scratchpad_write_note": Case({"content": "c"}, [(POST, f"/runs/{RUN}/scratchpad/notes", {})]),
    "scratchpad_raise_flag": Case(
        {"level": "info", "message": "m"}, [(POST, f"/runs/{RUN}/scratchpad/flags", {})]
    ),
    "scratchpad_set_status": Case(
        {"status": "done"},
        [(GET, f"/runs/{RUN}", RUN_ROW), (POST, f"/runs/{RUN}/scratchpad/status", {})],
    ),
    "scratchpad_write_summary": Case(
        {"summary": "s"}, [(POST, f"/runs/{RUN}/scratchpad/summary", {})]
    ),
    "scratchpad_read_agent": Case(
        {"target_agent_id": "segmentation_agent"},
        [(GET, f"/runs/session/{SESSION}/by-target/segmentation_agent", {"run": RUN_ROW})],
    ),
    "scratchpad_read_report": Case({}, [(GET, f"/runs/session/{SESSION}/scratchpad", {})]),
    # db_reads
    "get_all_patients_info": Case({}, [(GET, "/patients/", [])]),
    "get_all_studies_info": Case({}, [(GET, "/studies/", [])]),
    "get_model": Case({"model_id": 1}, [(GET, "/inference-models/1", {})]),
    "get_patient": Case({"patient_id": 1}, [(GET, "/patients/1", {})]),
    "search_patient": Case({"name": "x"}, [(GET, "/patients/search", [])]),
    "get_segmentation": Case({}, [(GET, "/segmentation-masks/", [])]),
    "get_segmentation_by_volume": Case({"volume_uid": "v1"}, [(GET, "/segmentation-masks/by-volume/v1", [])]),
    "get_segmentation_by_model": Case({"model_name": "m"}, [(GET, "/segmentation-masks/by-model/m", [])]),
    "get_invalidate_segmentations": Case({}, [(GET, "/segmentation-masks/unvalidated/", [])]),
    "search_segmentation": Case({}, [(GET, "/segmentation-masks/search/", [])]),
    "get_segmentation_stats": Case({}, [(GET, "/segmentation-masks/stats/", {})]),
    "get_study": Case({"study_uid": "s1"}, [(GET, "/studies/s1", {})]),
    "get_study_by_patient": Case({"patient_id": 1}, [(GET, "/studies/by_patient/1", [])]),
    "search_studies": Case({}, [(GET, "/studies/search/", [])]),
    "count_volumes_in_study": Case({"study_uid": "s1"}, [(GET, "/volumes/count/s1", {"count": 1})]),
    "get_volume": Case({}, [(GET, "/volumes/", [])]),
    "get_volumes_by_study": Case({"study_uid": "s1"}, [(GET, "/volumes/by_study/s1", [])]),
    "search_volumes": Case({}, [(GET, "/volumes/search/", [])]),
    "search_volumes_by_description": Case(
        {"words_to_include": ["ct"]},
        _population((GET, "/volumes/search/description/", [{"uid": "v1"}]),
                    (PUT, f"/cohorts/run/{RUN}/population", {"cohort_id": "c1"})),
    ),
    # literature
    "read_literature_context": Case(
        {},
        [(GET, f"/literature-reviews/session/{SESSION}", [{"literature_review_id": "l1", "created_at": "t"}]),
         (GET, "/literature-reviews/l1", {"result_summary": {}})],
    ),
    # population
    "write_inclusion_criteria": Case(
        {"inclusion_criteria": ["a"], "exclusion_criteria": [], "modality": "CT"},
        _population((PUT, f"/cohorts/run/{RUN}/population", {"cohort_id": "c1"})),
    ),
    "get_unique_series_descriptions": Case({}, [(GET, "/volumes/descriptions/unique/", ["CT"])]),
    "validate_population_anatomy": Case(
        {"hypothesis_body_region": "lung", "volume_details": [{"uid": "v1", "series_description": "CT chest"}]},
        _population((PUT, f"/cohorts/run/{RUN}/population", {"cohort_id": "c1"}),
                    (PUT, "/cohorts/c1/members", {"cohort_id": "c1"})),
    ),
    "save_final_output": Case(
        {"data": {"datasetlist": ["v1"]}},
        _population((PUT, f"/cohorts/run/{RUN}/population", {"cohort_id": "c1"}),
                    (PUT, "/cohorts/c1/members", COHORT)),
    ),
    "generate_targets": Case(
        {},
        [(GET, f"/cohorts/session/{SESSION}/population", COHORT),
         (GET, "/cohorts/c1/analysis-rows", [{"volume_series_instance_uid": "v1"}]),
         (POST, f"/runs/{RUN}/scratchpad/notes", {})],
    ),
    # reporting
    "create_report": Case({"source_text": "x"}, [(POST, "/reports/", {"report_id": "r1", "source_version": 1})]),
    "list_report_figures": Case({}, [(GET, f"/files/session/{SESSION}", [])]),
    "register_report_image": Case(
        {"report_id": "r1", "artifact_id": "f1", "image_key": "a.png"},
        [(POST, "/reports/r1/images", {"image_key": "a.png", "file_id": "f1"})],
    ),
    "list_latex_templates": Case({}),
    "get_latex_template": Case({"params": {"template_name": "short_study"}}),
    "get_modeling_results": Case(
        {}, [(GET, f"/feature-sets/session/{SESSION}", []), (GET, f"/model-results/session/{SESSION}/summary", [])]
    ),
    # segmentation
    "check_having_mask": Case({"volume_uid": "v1"}, [(GET, "/segmentation-masks/by-volume/v1", [])]),
}


def _broken_guide(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A template guide that cannot be read (a directory in its place)."""
    (tmp_path / "templates" / "template_guide.json").mkdir(parents=True)
    monkeypatch.setattr(template_tools, "TEMPLATES_DIR", str(tmp_path / "templates"))


def _down(args: dict | None = None, code: str = "upstream_error") -> Case:
    return Case(args or {}, api_down=True, error_code=code)


ERROR: dict[str, Case] = {
    **{name: _down(SUCCESS[name].args) for name in SUCCESS if SUCCESS[name].routes},
    # Their reads catch the HTTP error themselves and report it as unreachable.
    **{name: _down(SUCCESS[name].args, "unavailable")
       for name in ("read_agent_output", "read_all_agent_outputs", "scratchpad_read_agent",
                    "scratchpad_read_report")},
    # A scratchpad write that did not land.
    **{name: _down(SUCCESS[name].args, "write_failed")
       for name in ("scratchpad_register_agent", "scratchpad_write_note", "scratchpad_raise_flag",
                    "scratchpad_set_status", "scratchpad_write_summary")},
    "browse_workspace": Case({"params": {"directory": "missing"}}, error_code="not_found"),
    "list_latex_templates": Case(error_code="unavailable", setup=_broken_guide),
    "get_latex_template": Case({"params": {"template_name": "nope"}}, error_code="not_found"),
    "extract_cohort_labels": Case(
        routes=[(GET, f"/cohorts/session/{SESSION}/population", httpx.Response(404))],
        error_code="not_found",
    ),
}


# --- the harness


class RecordingEnviron(dict):
    """``os.environ`` that records every read of an identity variable."""

    def __init__(self, base, reads: list[str]):
        super().__init__(base)
        self.reads = reads

    def _note(self, key):
        if key in IDENTITY_ENV:
            self.reads.append(key)

    def get(self, key, default=None):
        self._note(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self._note(key)
        return super().__getitem__(key)

    def __contains__(self, key):
        self._note(key)
        return super().__contains__(key)


@pytest.fixture
def identity_reads(monkeypatch):
    reads: list[str] = []
    monkeypatch.setattr(os, "environ", RecordingEnviron(os.environ, reads))
    return reads


@pytest.fixture(scope="module")
def light_tools():
    catalog = catalog_module.build()
    catalog.validate()
    return {s.name: s for s in catalog.tools if s.pack != "delegation" and s.name not in HEAVY}


def _context(tmp_path: Path):
    return make_context(
        session_id=SESSION, run_id=RUN, attempt_id=ATTEMPT, node_id="n1",
        agent_type=POPULATION, workspace_dir=tmp_path,
    )


def _looks_like_an_error(result: Any) -> bool:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return False
    return isinstance(result, dict) and (
        result.get("status") == "error" or result.get("error") not in (None, False)
    )


def _run(case: Case, spec, fake_domain_api, monkeypatch, tmp_path) -> Any:
    if case.setup:
        case.setup(monkeypatch, tmp_path)
    for method, path, body in case.routes:
        response = body if isinstance(body, httpx.Response) else httpx.Response(200, json=body)
        fake_domain_api.route(method=method, path=path).mock(return_value=response)
    if case.api_down:
        fake_domain_api.route().mock(return_value=httpx.Response(503))
    return call_tool(spec, case.args, _context(tmp_path))


def test_every_light_tool_has_a_success_and_an_error_case(light_tools):
    assert len(light_tools) == 53
    missing_success = set(light_tools) - set(SUCCESS) - set(COVERED_ELSEWHERE)
    missing_error = set(light_tools) - set(ERROR) - set(COVERED_ELSEWHERE)
    assert not missing_success and not missing_error, (missing_success, missing_error)
    for name, test_file in COVERED_ELSEWHERE.items():
        assert name in (TESTS / test_file).read_text(encoding="utf-8"), (name, test_file)


@pytest.mark.parametrize("name", sorted(SUCCESS))
def test_success(name, light_tools, fake_domain_api, monkeypatch, tmp_path, identity_reads):
    result = _run(SUCCESS[name], light_tools[name], fake_domain_api, monkeypatch, tmp_path)

    assert not _looks_like_an_error(result), result
    assert not identity_reads, f"{name} read {sorted(set(identity_reads))} from the environment"


@pytest.mark.parametrize("name", sorted(ERROR))
def test_error(name, light_tools, fake_domain_api, monkeypatch, tmp_path, identity_reads):
    result = _run(ERROR[name], light_tools[name], fake_domain_api, monkeypatch, tmp_path)

    assert is_error_result(result), result  # the one envelope, never a bare dict or JSON text
    assert result["error_code"] == ERROR[name].error_code
    assert "Traceback" not in result["error_message"]
    assert not identity_reads, f"{name} read {sorted(set(identity_reads))} from the environment"


# --- the harness checks itself: a check that cannot fail proves nothing


def test_the_environ_guard_sees_an_identity_read(identity_reads):
    os.environ.get("SESSION_ID")
    os.getenv("RUN_ID")
    "WORKSPACE_DIR" in os.environ
    os.environ.get("PATH")  # not identity: not recorded
    assert identity_reads == ["SESSION_ID", "RUN_ID", "WORKSPACE_DIR"]


@pytest.mark.parametrize(
    "result",
    [
        {"status": "error", "message": "x"},
        json.dumps({"status": "error", "message": "x"}),
        {"error": "x"},
        {"error": True, "stage": "inference"},
    ],
)
def test_the_envelope_check_sees_a_bare_error(result):
    assert _looks_like_an_error(result)
    assert not is_error_result(result)


def test_every_light_tool_is_first_party_inline(light_tools):
    for name, spec in light_tools.items():
        assert (spec.execution, spec.trust, spec.isolation) == (
            Execution.INLINE, TrustLevel.FIRST_PARTY, IsolationLevel.INLINE
        ), name


def test_no_light_tool_imports_pycaret(light_tools):
    for name, spec in light_tools.items():
        module = Path(__import__(spec.handler.__module__, fromlist=["_"]).__file__)
        assert "pycaret" not in module.read_text(encoding="utf-8"), name
