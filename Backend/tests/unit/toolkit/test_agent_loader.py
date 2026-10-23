"""The agent.md loader (merged plan, Phase 3.1).

Files are written into a throwaway package on sys.path, so hooks are imported
the way the catalog imports them: as a module of the pack.
"""

from __future__ import annotations

import sys
import textwrap
import uuid
from pathlib import Path

import pytest

from agent_system.toolkit.agents.loader import AgentFileError, load_agent_file
from agent_system.toolkit.agents.spec import AgentKind
from agent_system.toolkit.catalog import build
from agent_system.toolkit.manifest import Gate, Resource
from agent_system.toolkit.ports import ExtractedArtifact

pytestmark = pytest.mark.unit

REPORTER = """\
---
agent_type: result_reporter_agent
kind: execution
description: Judges the hypothesis from the modeling results.
inputs:
  modeling: ModelingOutput
  features: FeatureTable?
outputs:
  summary: ResultSummary
allowed_tools: [get_modeling_results]
---
You judge the hypothesis.

Read the Node inputs first.
"""

REPORTER_HOOKS = """\
from agent_system.toolkit.ports import ExtractedArtifact

def extract_summary(result):
    return ExtractedArtifact(ref={"run_id": result["run_id"]})
"""


@pytest.fixture
def pack(tmp_path, monkeypatch):
    """A throwaway ``<name>.agentpacks`` package; returns (name, write)."""
    name = f"cubeai_agents_{uuid.uuid4().hex[:8]}"
    monkeypatch.syspath_prepend(str(tmp_path))

    def write(relative: str, source: str, *, package: bool = True) -> Path:
        path = tmp_path / name / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        for parent in [path.parent, *path.parent.parents]:
            if parent == tmp_path:
                break
            if package or parent != path.parent:
                (parent / "__init__.py").touch()
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        return path

    yield name, write
    for module in [m for m in sys.modules if m == name or m.startswith(f"{name}.")]:
        del sys.modules[module]


def _load(name: str, path: Path):
    package = ".".join((name, *path.parent.relative_to(path.parents[3]).parts))
    return load_agent_file(path, package)


def _reporter(write, front: str = REPORTER, hooks: str | None = REPORTER_HOOKS) -> Path:
    path = write("agentpacks/research/result_reporter_agent/agent.md", front)
    if hooks is not None:
        write("agentpacks/research/result_reporter_agent/hooks.py", hooks)
    return path


# --- a valid file


def test_a_valid_agent_file(pack):
    name, write = pack
    spec = _load(name, _reporter(write))

    assert spec.agent_type == "result_reporter_agent"
    assert spec.kind is AgentKind.EXECUTION
    assert spec.system_prompt == "You judge the hypothesis.\n\nRead the Node inputs first."
    assert spec.allowed_tools == ("get_modeling_results",)
    # Fields left out keep the AgentSpec defaults.
    assert spec.model_profile == "agent_default"
    assert spec.max_tool_iterations == 10
    assert spec.resources == frozenset({Resource.LLM})
    assert spec.resumable is True
    # Port labels: Type, Type? (optional); the extract comes from hooks.py.
    assert spec.inputs["modeling"].artifact_type == "ModelingOutput"
    assert spec.inputs["modeling"].required is True
    assert spec.inputs["features"].required is False
    summary = spec.outputs["summary"]
    assert summary.extract({"run_id": "r1"}) == ExtractedArtifact(ref={"run_id": "r1"})


def test_every_front_matter_field(pack):
    name, write = pack
    path = write(
        "agentpacks/research/segmentation_agent/agent.md",
        """\
        ---
        agent_type: segmentation_agent
        version: 2
        kind: execution
        description: Segments the cohort.
        model_profile: agent_default
        allowed_tools: [run_segmentation_with_guard, list_models, run_agent]
        delegates: [mask_resolution_agent]
        inputs: {cohort: Cohort}
        outputs: {masks: "MaskSet[]", extra: "ImageSet[]?"}
        max_tool_iterations: 6
        timeout_s: 10800
        resources: [llm]
        max_completion_retries: 1
        ui: segmentation_view
        resumable: true
        gates: [segmentation_review]
        gate_timeout_s: 86400
        max_context_tokens: 60000
        ---
        Segment.
        """,
    )
    write(
        "agentpacks/research/segmentation_agent/hooks.py",
        """\
        def extract_masks(result):
            return []

        def extract_extra(result):
            return None
        """,
    )

    spec = _load(name, path)

    assert spec.version == 2
    assert spec.delegates == ("mask_resolution_agent",)
    assert (spec.outputs["masks"].many, spec.outputs["masks"].required) == (True, True)
    assert (spec.outputs["extra"].many, spec.outputs["extra"].required) == (True, False)
    assert spec.max_tool_iterations == 6
    assert spec.timeout_s == 10800
    assert spec.max_completion_retries == 1
    assert spec.ui == "segmentation_view"
    assert spec.gates == frozenset({Gate.SEGMENTATION_REVIEW})
    assert spec.human_in_the_loop is True
    assert spec.gate_timeout_s == 86400
    assert spec.max_context_tokens == 60000


def test_every_hook_is_attached(pack):
    name, write = pack
    path = write(
        "agentpacks/research/study_population_planner_agent/agent.md",
        """\
        ---
        agent_type: study_population_planner_agent
        kind: execution
        description: Builds the cohort.
        outputs: {cohort: Cohort}
        max_completion_retries: 2
        ---
        Build the cohort.
        """,
    )
    write(
        "agentpacks/research/study_population_planner_agent/hooks.py",
        """\
        from pydantic import BaseModel

        class output_contract(BaseModel):
            cohort_id: str

        async def completion_check(ctx, output):
            return []

        def context_builder(task, inputs, ctx):
            return task

        def allowed_tools_fn(ctx, tools):
            return ["generate_targets"]

        def extract_cohort(result):
            return None
        """,
    )

    spec = _load(name, path)

    assert spec.has_dynamic_tools
    assert spec.tools_for(None, []) == ("generate_targets",)
    assert spec.completion_check.__name__ == "completion_check"
    assert spec.context_builder("task", {}, None) == "task"
    assert spec.output_contract.__name__ == "output_contract"
    assert spec.outputs["cohort"].extract is not None


def test_a_workflow_agent_gets_build_graph_from_its_hooks(pack):
    name, write = pack
    path = write(
        "agentpacks/research/literature_review_agent/agent.md",
        """\
        ---
        agent_type: literature_review_agent
        kind: execution
        description: Reviews the literature.
        workflow: [pico_analyst, query_builder]
        ---
        Review.
        """,
    )
    write(
        "agentpacks/research/literature_review_agent/hooks.py",
        "def build_graph(ctx):\n    return object()\n",
    )

    spec = _load(name, path)

    assert spec.workflow == ("pico_analyst", "query_builder")
    assert spec.build_graph.__name__ == "build_graph"


def test_a_directory_without_hooks_needs_no_package(pack):
    name, write = pack
    path = write(
        "agentpacks/research/clinical_target_intent_agent/agent.md",
        """\
        ---
        agent_type: clinical_target_intent_agent
        kind: internal
        parent: segmentation_agent
        description: Reads the clinical target from the question.
        ---
        Find the target.
        """,
        package=False,
    )

    spec = load_agent_file(path)

    assert (spec.kind, spec.parent, spec.resumable) == (AgentKind.INTERNAL, "segmentation_agent", False)


# --- refusals


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("agent_type: result_reporter_agent", "agent_type: result_agent", "must match its directory name"),
        ("allowed_tools:", "alowed_tools:", "alowed_tools: Extra inputs are not permitted"),
        ("kind: execution\n", "kind: execution\nhuman_in_the_loop: true\n", "derived from gates"),
        ("kind: execution\n", "kind: execution\ncompletion_check: x\n", "define it in hooks.py"),
        ("kind: execution\n", "kind: execution\nsystem_prompt: x\n", "body of agent.md"),
        ("kind: execution\n", "kind: execution\nmax_tool_iterations: '12'\n", "max_tool_iterations: Input should be a valid integer"),
        ("allowed_tools: [get_modeling_results]", "allowed_tools: get_modeling_results", "allowed_tools: Input should be a valid list"),
        ("kind: execution", "kind: planner", "kind='planner'"),
        ("description: Judges the hypothesis from the modeling results.\n", "", "description: Field required"),
        ("summary: ResultSummary", "summary: Result Summary", "'Result Summary' is not Type, Type\\[\\] or Type\\?"),
        ("summary: ResultSummary", "summary: ResultSummary[]!", "is not Type"),
        ("kind: execution\n", "kind: execution\ngates: [manual_review]\n", "gates='manual_review'"),
        ("kind: execution\n", "kind: execution\ngates: [tool_approval]\n", "implicit gates"),
        ("allowed_tools: [get_modeling_results]", "allowed_tools: [a, a]", "more than once"),
        ("You judge the hypothesis.\n\nRead the Node inputs first.\n", "", "system_prompt must not be empty"),
    ],
)  # fmt: skip
def test_a_bad_front_matter_is_refused_with_the_file_name(pack, old, new, match):
    name, write = pack
    assert old in REPORTER
    path = _reporter(write, REPORTER.replace(old, new))

    with pytest.raises(AgentFileError, match=match) as caught:
        _load(name, path)

    assert caught.value.path == path
    assert str(caught.value).startswith(str(path))


def test_a_file_without_front_matter(pack):
    name, write = pack
    path = _reporter(write, "You judge the hypothesis.\n", hooks=None)

    with pytest.raises(AgentFileError, match="must start with a '---' line"):
        load_agent_file(path)


def test_an_extract_hook_without_its_port(pack):
    name, write = pack
    hooks = REPORTER_HOOKS + "\ndef extract_sumary(result):\n    return None\n"
    path = _reporter(write, hooks=hooks)

    with pytest.raises(AgentFileError, match="defines extract_sumary but there is no output port 'sumary'"):
        _load(name, path)


def test_allowed_tools_in_both_places(pack):
    name, write = pack
    hooks = REPORTER_HOOKS + "\ndef allowed_tools_fn(ctx, tools):\n    return []\n"
    path = _reporter(write, hooks=hooks)

    with pytest.raises(AgentFileError, match="not both"):
        _load(name, path)


def test_hooks_need_an_init_next_to_them(pack):
    name, write = pack
    path = write(
        "agentpacks/research/result_reporter_agent/agent.md", REPORTER, package=False
    )
    write("agentpacks/research/result_reporter_agent/hooks.py", REPORTER_HOOKS, package=False)

    with pytest.raises(AgentFileError, match="needs an __init__.py next to it"):
        _load(name, path)


def test_hooks_need_a_package_name(pack):
    _, write = pack
    path = _reporter(write)

    with pytest.raises(AgentFileError, match="no package name was given"):
        load_agent_file(path)


def test_a_broken_hooks_import_is_not_hidden(pack):
    name, write = pack
    path = _reporter(write, hooks="import cubeai_module_that_does_not_exist\n")

    with pytest.raises(ModuleNotFoundError, match="cubeai_module_that_does_not_exist"):
        _load(name, path)


# --- through the catalog


def test_the_catalog_loads_agent_files_by_default(pack):
    name, write = pack
    _reporter(write)

    catalog = build(packages=[f"{name}.agentpacks"], entry_point_groups=())

    spec = catalog.agents.get("result_reporter_agent")
    assert spec.outputs["summary"].extract({"run_id": "r1"}).ref == {"run_id": "r1"}
