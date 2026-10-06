"""The moved tools, as the catalog discovers them (merged plan, Phase 2.1).

Every tool left ``@mcp.tool`` for ``@tool`` under its old name, with the
interim manifest of ``toolpacks/common/manifests.py``.
"""

from __future__ import annotations

import inspect
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from agent_system.toolkit import catalog as catalog_module
from agent_system.toolkit.manifest import Cost, Execution, SideEffects, Visibility

pytestmark = pytest.mark.unit

BACKEND_ROOT = Path(__file__).resolve().parents[3]
TOOLPACKS_ROOT = BACKEND_ROOT / "agent_system" / "toolpacks"

# Every tool Backend/mcp registered, by pack. A name may never change here
# (except Plan 7's rename of the 5 PyCaret tools): agent prompts call tools by name.
MOVED_TOOLS: dict[str, set[str]] = {
    "bookkeeping": {
        "write_agent_output", "read_agent_output", "read_all_agent_outputs",
        "browse_workspace", "read_agent_task_context",
        "scratchpad_register_agent", "scratchpad_write_note", "scratchpad_raise_flag",
        "scratchpad_set_status", "scratchpad_write_summary", "scratchpad_read_agent",
        "scratchpad_read_report",
    },
    "db_reads": {
        "get_all_patients_info", "get_all_studies_info", "get_model", "get_patient",
        "search_patient", "get_segmentation", "get_segmentation_by_volume",
        "get_segmentation_by_model", "get_invalidate_segmentations", "search_segmentation",
        "get_segmentation_stats", "get_study", "get_study_by_patient", "search_studies",
        "count_volumes_in_study", "get_volume", "get_volumes_by_study", "search_volumes",
        "search_volumes_by_description",
    },
    "literature": {"read_literature_context"},
    "modeling": {
        "train_classifier", "run_classifier_inference", "train_regressor",
        "run_regression_inference", "refit_model",
    },
    "population": {
        "write_inclusion_criteria", "get_unique_series_descriptions", "extract_cohort_labels",
        "validate_population_anatomy", "save_final_output", "generate_targets",
    },
    "radiomics": {"radiomics_extract_features", "radiomics_analyze_importance"},
    "reporting": {
        "create_report", "latex_to_pdf", "list_report_figures", "register_report_image",
        "list_latex_templates", "get_latex_template", "get_modeling_results",
    },
    "segmentation": {"run_segmentation", "check_having_mask", "list_models"},
}
# Tools Plan 2 adds on top of the moved ones (Phase 2.4).
ADDED_TOOLS: dict[str, set[str]] = {
    "segmentation": {"run_segmentation_with_guard"},
    "literature": {
        "search_pubmed", "search_arxiv", "search_semantic_scholar", "search_europe_pmc",
        "score_paper_relevance", "save_literature_output",
    },
}
# Plan 7 turns these into cost="heavy" job tools; until then INTERIM_HEAVY.
INTERIM_HEAVY_TOOLS = {
    "radiomics_extract_features", "radiomics_analyze_importance", "run_segmentation",
    "run_segmentation_with_guard", *MOVED_TOOLS["modeling"],
}
HARNESS_TOOLS = {"scratchpad_register_agent", "scratchpad_set_status"}


@pytest.fixture(scope="module")
def catalog():
    built = catalog_module.build()
    built.validate()
    return built


@pytest.fixture(scope="module")
def pack_tools(catalog):
    return {spec.name: spec for spec in catalog.tools if spec.pack != "delegation"}


def test_the_catalog_finds_every_moved_tool_in_its_pack(catalog, pack_tools):
    assert sum(len(names) for names in MOVED_TOOLS.values()) == 55
    expected = {pack: names | ADDED_TOOLS.get(pack, set()) for pack, names in MOVED_TOOLS.items()}
    found = {pack: {spec.name for spec in specs} for pack, specs in catalog.tools.by_pack().items()}
    found.pop("delegation")  # run_agent, Plan 1
    assert found == expected
    assert len(pack_tools) == 55 + sum(len(names) for names in ADDED_TOOLS.values())


def test_the_interim_heavy_tools_are_marked_for_plan_7(pack_tools):
    interim = {
        name
        for name, spec in pack_tools.items()
        if spec.side_effects is SideEffects.COMPUTE and spec.cost is Cost.MEDIUM
    }
    assert interim == INTERIM_HEAVY_TOOLS
    assert all(pack_tools[name].execution is Execution.INLINE for name in interim)


def test_every_moved_tool_is_inline_and_none_is_heavy_yet(pack_tools):
    assert {spec.execution for spec in pack_tools.values()} == {Execution.INLINE}
    assert not any(spec.cost is Cost.HEAVY for spec in pack_tools.values())


def test_the_graph_tools_are_the_two_with_ports(pack_tools):
    graph = {name for name, spec in pack_tools.items() if Visibility.GRAPH in spec.visibility}
    assert graph == {"run_segmentation_with_guard", "latex_to_pdf"}
    segmentation = pack_tools["run_segmentation_with_guard"]
    assert segmentation.inputs["cohort"].artifact_type == "Cohort"
    assert segmentation.outputs["masks"].artifact_type == "MaskSet"
    report = pack_tools["latex_to_pdf"]
    assert report.inputs["report"].artifact_type == report.outputs["report"].artifact_type == "ReportDocument"


def test_every_light_tool_states_its_budget_and_scopes(pack_tools):
    """Phase 2.4: a full manifest on every light tool. A tool that reads or
    writes nothing outside the process may leave data_scopes empty, but every
    one states its timeout instead of inheriting the default."""
    light = {n: s for n, s in pack_tools.items() if n not in INTERIM_HEAVY_TOOLS}
    assert len(light) == 53
    implicit = [
        n for n, s in light.items()
        # getsource() of an @tool function starts at its decorator.
        if not re.search(r"timeout_s=|LITERATURE_TOOL", inspect.getsource(s.handler).split("async def")[0])
    ]
    assert not implicit
    # It keeps the run's files in process memory (literature/run_state.py).
    in_memory_writers = {"save_literature_output"}
    writers_without_scope = [
        n for n, s in light.items()
        if s.side_effects is SideEffects.WRITES and not s.write_scopes and n not in in_memory_writers
    ]
    assert not writers_without_scope


def test_the_tools_that_touch_patient_data_say_so(pack_tools):
    for name in ("get_patient", "get_volume", "check_having_mask", "extract_cohort_labels",
                 "validate_population_anatomy", "run_segmentation_with_guard"):
        assert pack_tools[name].phi, name
    for name in ("get_model", "score_paper_relevance", "list_latex_templates"):
        assert not pack_tools[name].phi, name


def test_only_the_run_bookkeeping_is_harness_only(pack_tools):
    harness = {name for name, spec in pack_tools.items() if spec.visibility == {Visibility.HARNESS}}
    assert harness == HARNESS_TOOLS


def test_the_main_tools_are_read_only(pack_tools):
    for name, spec in pack_tools.items():
        if Visibility.MAIN in spec.visibility:
            assert spec.side_effects is SideEffects.NONE and spec.read_only, name


def test_no_pack_still_speaks_mcp():
    # Code lines only: docstrings may still tell the history.
    legacy = re.compile(
        r"^\s*(?:@mcp\.tool\b"
        r"|(?:from|import)\s+(?:mcp|fastmcp|tool_logging|internal_emitter|service_urls)\b"
        r"|from\s+\.+\s+import\s+mcp\b"
        r"|.*\b(?:log_tool_state|broadcast_ws_message)\s*\()",
        re.M,
    )
    offenders = [
        f"{path.relative_to(TOOLPACKS_ROOT)}: {match.group(0).strip()}"
        for path in TOOLPACKS_ROOT.rglob("*.py")
        for match in legacy.finditer(path.read_text(encoding="utf-8"))
    ]
    assert not offenders
    assert not (BACKEND_ROOT / "mcp").exists()


def test_the_container_root_builds_the_same_catalog(tmp_path):
    """In the container agent_system is sys.path[0]: the toolkit and the packs
    load as bare ``toolkit`` / ``toolpacks``. A pack that imported the toolkit
    under the other root would load a second ToolSpec class and drop out."""
    script = textwrap.dedent(
        """
        import sys
        sys.path[:0] = [sys.argv[1], sys.argv[2]]
        from toolkit import catalog
        built = catalog.build()
        built.validate()
        print(len([s for s in built.tools if s.pack != "delegation"]))
        print("agent_system.toolkit" in sys.modules)
        """
    )
    env = os.environ | {
        "BACKEND_STORAGE_ROOT": str(tmp_path),
        "AGENTS_WORKSPACE_DIR": str(tmp_path / "hypotheses"),
        "BACKEND_TEMP_DIR": str(tmp_path / "temp"),
        "BACKEND_LOG_DIR": str(tmp_path / "logs"),
    }
    result = subprocess.run(
        [sys.executable, "-c", script, str(BACKEND_ROOT / "agent_system"), str(BACKEND_ROOT)],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["62", "False"]  # 55 moved + 7 added
