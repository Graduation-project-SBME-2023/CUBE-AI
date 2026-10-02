"""The catalog: discovery, validation, resolve, index, describe (merged plan, Phase 1.3 step 3)."""

from __future__ import annotations

import copy
import importlib
import json
import sys
import textwrap
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from agent_system.toolkit import catalog as catalog_module
from agent_system.toolkit.agents.registry import AgentRegistry
from agent_system.toolkit.agents.spec import AgentSpec
from agent_system.toolkit.artifacts.base import ArtifactType
from agent_system.toolkit.artifacts.builtin import COHORT, CohortRef, CohortSummary, builtin_registry
from agent_system.toolkit.catalog import (
    PACK_PACKAGES,
    Catalog,
    CatalogError,
    UnknownRefError,
    build,
)
from agent_system.toolkit.ports import ExtractedArtifact, PortSpec
from agent_system.toolkit.tools.registry import ToolRegistry
from agent_system.toolkit.tools.spec import ToolSpec

pytestmark = pytest.mark.unit


# --- a small, valid catalog


class _CohortArgs(BaseModel):
    cohort_id: str


async def _noop(**_: Any) -> dict[str, Any]:
    return {}


def _masks(result: Any) -> ExtractedArtifact:
    return ExtractedArtifact(ref={"segmentation_batch_id": result["run"]})


def _tool(name: str, visibility: set[str], **overrides: Any) -> ToolSpec:
    fields: dict[str, Any] = {
        "name": name,
        "description": f"{name.replace('_', ' ').capitalize()}.\nMore detail here.",
        "pack": "test",
        "handler": _noop,
        "input_model": _CohortArgs,
        "side_effects": "none",
        "cost": "light",
        "execution": "inline",
        "visibility": visibility,
        "read_only": True,
    }
    fields.update(overrides)
    return ToolSpec(**fields)


GET_PATIENT = _tool("get_patient", {"main", "agent"})
LIST_MODELS = _tool("list_models", {"agent"})
SET_STATUS = _tool("scratchpad_set_status", {"harness"}, side_effects="writes", read_only=False)
RUN_AGENT = _tool("run_agent", {"agent"}, pack="delegation")
PLAN = _tool("plan", {"main"}, cost="medium")
RECOVER = _tool("recover", {"main"}, cost="medium")
SEGMENT = _tool(
    "segment",
    {"graph", "agent"},
    side_effects="compute",
    read_only=False,
    inputs={"cohort": PortSpec("Cohort", param="cohort_id")},
    outputs={"masks": PortSpec("MaskSet", many=True, extract=_masks)},
)
TOOLS = [GET_PATIENT, LIST_MODELS, SET_STATUS, RUN_AGENT, PLAN, RECOVER, SEGMENT]


def _agent(agent_type: str, kind: str, **overrides: Any) -> AgentSpec:
    fields: dict[str, Any] = {
        "agent_type": agent_type,
        "description": f"{agent_type}.",
        "kind": kind,
        "system_prompt": "Prompt.",
    }
    fields.update(overrides)
    return AgentSpec(**fields)


MAIN = _agent("main_agent", "control", allowed_tools=["get_patient", "plan", "recover"])
COMPOSER = _agent("composer", "control", allowed_tools=lambda ctx, tools: ["get_patient"])
SEGMENTATION = _agent(
    "segmentation_agent",
    "execution",
    allowed_tools=["run_agent", "list_models", "segment"],
    delegates=["mask_resolution_agent"],
    inputs={"cohort": PortSpec("Cohort")},
    outputs={
        "masks": PortSpec("MaskSet", extract=_masks),
        "extra": PortSpec("ImageSet", required=False, extract=_masks),
    },
)
MASK_RESOLUTION = _agent(
    "mask_resolution_agent", "internal", parent="segmentation_agent", allowed_tools=["list_models"]
)
AGENTS = [MAIN, COMPOSER, SEGMENTATION, MASK_RESOLUTION]


def _catalog(
    tools: list[ToolSpec] = TOOLS, agents: list[AgentSpec] = AGENTS, **kwargs: Any
) -> Catalog:
    return Catalog(tools=ToolRegistry(tools), agents=AgentRegistry(agents), **kwargs)


def _replace(items: list[Any], name: str, **changes: Any) -> list[Any]:
    return [copy.replace(item, **changes) if item.name == name else item for item in items]


def test_the_valid_catalog_has_no_problems():
    catalog = _catalog()

    assert catalog.problems() == []
    catalog.validate()


# --- validate: names, tools, ports, policy, artifact types


def test_a_name_cannot_be_both_a_tool_and_an_agent():
    tools = [*TOOLS, _tool("composer", {"agent"})]

    assert _catalog(tools).problems() == ["'composer' is both a tool and an agent"]


def test_manifest_rules_are_enforced():
    tools = _replace(TOOLS, "list_models", read_only=False)

    assert _catalog(tools).problems() == [
        'tool \'list_models\': side_effects="none" requires read_only=True [side_effects_read_only]'
    ]


def test_ports_must_name_known_artifact_types():
    tools = _replace(TOOLS, "segment", outputs={"heatmap": PortSpec("AttentionMap", extract=_masks)})
    agents = _replace(AGENTS, "segmentation_agent", inputs={"scan": PortSpec("Scan")})

    assert _catalog(tools, agents).problems() == [
        "tool 'segment': output port 'heatmap' names unknown artifact type 'AttentionMap'",
        "agent 'segmentation_agent': input port 'scan' names unknown artifact type 'Scan'",
    ]


def test_pack_types_make_ports_valid():
    attention = ArtifactType(
        name="AttentionMap",
        description="An attention map.",
        ref_model=CohortRef,
        summary_model=CohortSummary,
        phi_default=True,
    )
    types = builtin_registry()
    types.register(attention)
    tools = _replace(TOOLS, "segment", outputs={"heatmap": PortSpec("AttentionMap", extract=_masks)})

    assert _catalog(tools, artifact_types=types).problems() == []


def test_policy_hooks_report_per_tool():
    def no_harness_tools(spec: ToolSpec) -> list[str]:
        return ["harness tools are under review"] if spec.visible_to("harness") else []

    assert _catalog(policy_hooks=(no_harness_tools,)).problems() == [
        "tool 'scratchpad_set_status': harness tools are under review"
    ]


def test_artifact_type_problems_are_included():
    types = builtin_registry()
    types.register(copy.replace(COHORT, name="LabelledCohort", parents={"Cohrt"}))

    assert _catalog(artifact_types=types).problems() == [
        "artifact type 'LabelledCohort' names unknown parent 'Cohrt'"
    ]


# --- validate: agents


@pytest.mark.parametrize(
    ("agent", "changes", "problem"),
    [
        ("segmentation_agent", {"allowed_tools": ["segmnt"]}, "allowlist names unknown tool 'segmnt'"),
        (
            "segmentation_agent",
            {"allowed_tools": ["scratchpad_set_status"]},
            "may not use 'scratchpad_set_status': not visible to agent",
        ),
        ("main_agent", {"allowed_tools": ["list_models"]}, "may not use 'list_models': not visible to main"),
        ("composer", {"allowed_tools": ["get_patient", "plan"]}, "a control agent may not use 'plan'"),
        ("main_agent", {"inputs": {"cohort": PortSpec("Cohort")}}, "only execution agents have ports, not control agents"),
        ("mask_resolution_agent", {"outputs": {"m": PortSpec("MaskSet")}}, "only execution agents have ports, not internal agents"),
        ("composer", {"parent": "main_agent"}, "only internal agents have a parent, not control agents"),
        ("segmentation_agent", {"delegates": ["mask_agent"]}, "unknown delegate 'mask_agent'"),
        ("segmentation_agent", {"delegates": ["composer"]}, "delegate 'composer' is not an internal agent"),
    ],
)  # fmt: skip
def test_agent_rules(agent, changes, problem):
    assert _catalog(agents=_replace(AGENTS, agent, **changes)).problems() == [
        f"agent {agent!r}: {problem}"
    ]


def test_main_may_use_plan_and_recover_but_not_run_agent():
    agents = _replace(AGENTS, "main_agent", allowed_tools=["plan", "recover", "run_agent"])

    assert _catalog(agents=agents).problems() == [
        "agent 'main_agent': may not use 'run_agent': not visible to main",
        "agent 'main_agent': a control agent may not use 'run_agent'",
    ]


def test_an_unknown_parent():
    agents = _replace(AGENTS, "mask_resolution_agent", parent="segmentation")

    assert _catalog(agents=agents).problems() == [
        "agent 'segmentation_agent': delegate 'mask_resolution_agent' has parent "
        "'segmentation', not 'segmentation_agent'",
        "agent 'mask_resolution_agent': unknown parent 'segmentation'",
    ]


def test_a_control_agent_cannot_own_internal_agents():
    critic = _agent("graph_critic", "internal", parent="composer")
    agents = [*_replace(AGENTS, "composer", delegates=["graph_critic"]), critic]

    assert _catalog(agents=agents).problems() == [
        "agent 'composer': a control agent has no delegates, not ['graph_critic']",
        "agent 'graph_critic': parent 'composer' is a control agent; "
        "only execution and internal agents start internal agents",
    ]


def test_an_internal_agent_can_own_internal_agents():
    checker = _agent("label_checker_agent", "internal", parent="mask_resolution_agent")
    agents = [
        *_replace(AGENTS, "mask_resolution_agent", delegates=["label_checker_agent"]),
        checker,
    ]

    assert _catalog(agents=agents).problems() == []


def test_delegation_cycles():
    agents = _replace(AGENTS, "mask_resolution_agent", delegates=["segmentation_agent"])

    assert _catalog(agents=agents).problems() == [
        "agent 'segmentation_agent': is in a delegation cycle",
        "agent 'mask_resolution_agent': delegate 'segmentation_agent' is not an internal agent",
        "agent 'mask_resolution_agent': is in a delegation cycle",
    ]


def test_dynamic_allowlists_are_checked_at_run_time_not_here():
    agents = _replace(AGENTS, "composer", allowed_tools=lambda ctx, tools: ["plan"])

    assert _catalog(agents=agents).problems() == []


def test_validate_raises_with_every_problem():
    tools = [*TOOLS, _tool("composer", {"agent"})]
    agents = _replace(AGENTS, "mask_resolution_agent", parent=None)

    with pytest.raises(CatalogError) as caught:
        _catalog(tools, agents).validate()

    assert caught.value.problems == [
        "'composer' is both a tool and an agent",
        "agent 'segmentation_agent': delegate 'mask_resolution_agent' has parent None, "
        "not 'segmentation_agent'",
        "agent 'mask_resolution_agent': an internal agent needs a parent",
    ]
    assert str(caught.value).startswith("3 catalog problem(s):\n- 'composer'")


# --- resolve


def test_resolve():
    catalog = _catalog()

    assert catalog.resolve("segment") is SEGMENT
    assert catalog.resolve("segmentation_agent") is SEGMENTATION
    with pytest.raises(UnknownRefError, match="unknown tool or agent 'segmenter'"):
        catalog.resolve("segmenter")


# --- index


def test_main_index():
    assert _catalog().index("main") == [
        {"name": "get_patient", "kind": "tool", "description": "Get patient.", "cost": "light", "use": ["call"]},
        {"name": "plan", "kind": "tool", "description": "Plan.", "cost": "medium", "use": ["call"]},
        {"name": "recover", "kind": "tool", "description": "Recover.", "cost": "medium", "use": ["call"]},
        {
            "name": "segment",
            "kind": "tool",
            "description": "Segment.",
            "inputs": {"cohort": "Cohort"},
            "outputs": {"masks": "MaskSet[]"},
            "cost": "light",
            "use": ["node"],
        },
        {
            "name": "segmentation_agent",
            "kind": "agent",
            "description": "segmentation_agent.",
            "inputs": {"cohort": "Cohort"},
            "outputs": {"masks": "MaskSet", "extra": "ImageSet?"},
            "use": ["node"],
        },
    ]  # fmt: skip


def test_composer_index_lists_only_graph_nodes():
    assert [entry["name"] for entry in _catalog().index("composer")] == [
        "segment",
        "segmentation_agent",
    ]


def test_a_tool_can_be_called_and_submitted():
    tools = _replace(TOOLS, "segment", visibility={"graph", "agent", "main"})

    (segment,) = [e for e in _catalog(tools).index() if e["name"] == "segment"]
    assert segment["use"] == ["call", "node"]


def test_index_refuses_an_unknown_audience():
    with pytest.raises(ValueError):
        _catalog().index("planner")


# --- describe


def test_describe_a_tool():
    described = _catalog().describe("segment")

    assert described["kind"] == "tool"
    assert described["pack"] == "test"
    assert described["args_schema"]["required"] == ["cohort_id"]
    assert described["inputs"] == {
        "cohort": {"type": "Cohort", "many": False, "required": True, "param": "cohort_id"}
    }
    assert described["outputs"] == {"masks": {"type": "MaskSet", "many": True, "required": True}}
    assert described["manifest"]["visibility"] == ["agent", "graph"]
    assert described["manifest"]["side_effects"] == "compute"
    assert described["manifest"]["phi"] is False
    json.dumps(described)


class _Proposal(BaseModel):
    summary: str


def test_describe_an_agent():
    agents = _replace(AGENTS, "composer", output_contract=_Proposal)
    catalog = _catalog(agents=agents)

    segmentation = catalog.describe("segmentation_agent")
    composer = catalog.describe("composer")

    assert segmentation["kind"] == "agent"
    assert segmentation["agent_kind"] == "execution"
    assert segmentation["tools"] == ["run_agent", "list_models", "segment"]
    assert segmentation["delegates"] == ["mask_resolution_agent"]
    assert segmentation["resources"] == ["llm"]
    assert segmentation["output_schema"] is None
    assert composer["tools"] == "dynamic"
    assert composer["output_schema"]["title"] == "_Proposal"
    json.dumps(segmentation)
    json.dumps(composer)


# --- build: discovery


def test_default_pack_packages_follow_the_toolkit_import_root():
    assert PACK_PACKAGES == (
        "agent_system.toolkit.delegation",
        "agent_system.toolpacks",
        "agent_system.agentpacks",
    )


def test_missing_pack_packages_are_skipped():
    catalog = build(packages=["cubeai_no_such_pack"], entry_point_groups=())

    assert (len(catalog.tools), len(catalog.agents), len(catalog.artifact_types)) == (0, 0, 9)


@pytest.fixture
def pack_root(tmp_path, monkeypatch):
    """A throwaway package ``<name>.toolpacks`` on sys.path; returns (name, writer)."""
    name = f"cubeai_test_{uuid.uuid4().hex[:8]}"
    monkeypatch.syspath_prepend(str(tmp_path))

    def write(relative: str, source: str = "") -> Path:
        path = tmp_path / name / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        for parent in [path.parent, *path.parent.parents]:
            if parent == tmp_path:
                break
            (parent / "__init__.py").touch()
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        return path

    yield name, write
    for module in [m for m in sys.modules if m == name or m.startswith(f"{name}.")]:
        del sys.modules[module]


TOOL_SOURCE = """
    from agent_system.toolkit.tools.registry import tool

    READ = dict(side_effects="none", cost="light", execution="inline",
                visibility={"main", "agent"}, read_only=True)

    @tool(**READ)
    async def NAME(patient_id: str) -> dict:
        '''Read NAME.'''
        return {}
"""


def test_build_discovers_tools_agents_and_types_in_nested_modules(pack_root):
    name, write = pack_root
    write("toolpacks/db_reads/patients.py", TOOL_SOURCE.replace("NAME", "get_patient"))
    write("toolpacks/db_reads/deep/studies.py", TOOL_SOURCE.replace("NAME", "get_study"))
    write(
        "toolpacks/imaging/definitions.py",
        """
        from agent_system.toolkit.artifacts.base import ArtifactType
        from agent_system.toolkit.artifacts.builtin import COHORT, CohortRef, CohortSummary
        from agent_system.toolkit.agents.spec import AgentSpec

        SCAN = ArtifactType(name="Scan", description="A scan.", ref_model=CohortRef,
                            summary_model=CohortSummary, phi_default=True)
        READER = AgentSpec(agent_type="scan_reader", description="Reads scans.",
                           kind="execution", system_prompt="Read.")
        """,
    )

    catalog = build(packages=[f"{name}.toolpacks"], entry_point_groups=())

    assert catalog.tools.names() == {"get_patient", "get_study"}
    assert catalog.tools.get("get_study").pack == "db_reads"  # from the module path
    assert catalog.agents.names() == {"scan_reader"}
    assert "Scan" in catalog.artifact_types
    assert len(catalog.artifact_types) == 10  # the built-ins are always there
    catalog.validate()


def test_a_tool_imported_into_another_module_is_counted_once(pack_root):
    name, write = pack_root
    write("toolpacks/db_reads/patients.py", TOOL_SOURCE.replace("NAME", "get_patient"))
    write("toolpacks/db_reads/reexport.py", "from .patients import get_patient\n")

    assert build(packages=[f"{name}.toolpacks"], entry_point_groups=()).tools.names() == {
        "get_patient"
    }


def test_two_tools_with_one_name_are_refused(pack_root):
    name, write = pack_root
    write("toolpacks/a/one.py", TOOL_SOURCE.replace("NAME", "get_patient"))
    write("toolpacks/b/two.py", TOOL_SOURCE.replace("NAME", "get_patient"))

    with pytest.raises(CatalogError, match="tool 'get_patient' is already registered"):
        build(packages=[f"{name}.toolpacks"], entry_point_groups=())


def test_a_broken_pack_module_is_not_skipped(pack_root):
    name, write = pack_root
    write("toolpacks/db_reads/broken.py", "import cubeai_module_that_does_not_exist\n")

    with pytest.raises(ModuleNotFoundError, match="cubeai_module_that_does_not_exist"):
        build(packages=[f"{name}.toolpacks"], entry_point_groups=())


def test_agent_files_go_through_the_loader(pack_root):
    name, write = pack_root
    first = write("agentpacks/research/seg_agent/agent.md", "---\nagent_type: seg_agent\n---\n")
    second = write("agentpacks/core/main_agent/agent.md", "---\nagent_type: main_agent\n---\n")
    loaded: list[Path] = []

    def load(path: Path) -> AgentSpec:
        loaded.append(path)
        return _agent(path.parent.name, "execution")

    catalog = build(packages=[f"{name}.agentpacks"], entry_point_groups=(), load_agent_file=load)

    assert sorted(loaded) == sorted([first, second])
    assert catalog.agents.names() == {"seg_agent", "main_agent"}


def test_agent_files_without_a_loader_are_an_error(pack_root):
    name, write = pack_root
    write("agentpacks/research/seg_agent/agent.md", "---\n---\n")

    with pytest.raises(CatalogError, match=r"found 1 agent\.md file\(s\) but no agent loader"):
        build(packages=[f"{name}.agentpacks"], entry_point_groups=())


def test_entry_point_packs(pack_root, monkeypatch):
    name, write = pack_root
    write("extra/toolpacks/test_pack/tools.py", TOOL_SOURCE.replace("NAME", "search_pubmed"))
    extra = importlib.import_module(f"{name}.extra")
    published = {"cubeai.toolpacks": [SimpleNamespace(name="extra", load=lambda: extra)]}
    monkeypatch.setattr(catalog_module, "entry_points", lambda group: published.get(group, []))

    catalog = build(packages=[])

    assert catalog.tools.names() == {"search_pubmed"}
    assert catalog.tools.get("search_pubmed").pack == "test_pack"


def test_an_entry_point_must_name_a_module(monkeypatch):
    published = [SimpleNamespace(name="bad", load=lambda: object())]
    monkeypatch.setattr(catalog_module, "entry_points", lambda group: published)

    with pytest.raises(CatalogError, match="entry point 'bad' .* is not a module"):
        build(packages=[])


def test_policy_hooks_are_passed_through():
    def hook(spec: ToolSpec) -> list[str]:
        return []

    assert build(packages=[], entry_point_groups=(), policy_hooks=[hook]).policy_hooks == (hook,)

