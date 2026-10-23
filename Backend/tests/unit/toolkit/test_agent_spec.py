"""AgentSpec field checks and derived fields (merged plan, Phase 1.1 step 4).

The rules that need other specs (delegates, cycles, tool names, ports on
control agents, parents) belong to catalog.validate() and are tested there.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from agent_system.toolkit.agents.spec import AgentKind, AgentSpec
from agent_system.toolkit.ports import ExtractedArtifact, PortSpec
from agent_system.toolkit.manifest import Gate, Resource
from agent_system.toolkit.tools.spec import ToolSpec

pytestmark = pytest.mark.unit

CTX: Any = SimpleNamespace(session_id="s1", run_id="r1")


class _Proposal(BaseModel):
    summary: str


def _masks(result: Any) -> ExtractedArtifact:
    return ExtractedArtifact(ref={"segmentation_batch_id": result["run"]})


async def _handoff_issues(ctx: Any, output: Any) -> list[str]:
    return []


async def _handler(**_: Any) -> dict[str, Any]:
    return {"status": "ok"}


class _Args(BaseModel):
    patient_id: str


def _tool(name: str, visibility: set[str]) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} tool.",
        pack="db_reads",
        handler=_handler,
        input_model=_Args,
        side_effects="none",
        cost="light",
        execution="inline",
        visibility=visibility,
        read_only=True,
    )


def _agent(**overrides: Any) -> AgentSpec:
    fields: dict[str, Any] = {
        "agent_type": "segmentation_agent",
        "description": "Selects and performs segmentation workflows.",
        "kind": "execution",
        "system_prompt": "You segment the cohort.",
        "allowed_tools": ["run_segmentation_with_guard", "list_models"],
        "delegates": ["clinical_target_intent_agent", "mask_resolution_agent"],
        "inputs": {"cohort": PortSpec("Cohort")},
        "outputs": {"masks": PortSpec("MaskSet", extract=_masks)},
    }
    fields.update(overrides)
    return AgentSpec(**fields)


def test_defaults_and_normalisation():
    spec = _agent()

    assert spec.kind is AgentKind.EXECUTION
    assert spec.version == 1
    assert spec.allowed_tools == ("run_segmentation_with_guard", "list_models")
    assert spec.delegates == ("clinical_target_intent_agent", "mask_resolution_agent")
    assert spec.model_profile == "agent_default"
    assert spec.max_tool_iterations == 10
    assert spec.timeout_s == 3600.0
    assert spec.resources == frozenset({Resource.LLM})
    assert spec.completion_check is None
    assert spec.max_completion_retries == 0
    assert spec.human_in_the_loop is False
    assert spec.parent is None
    assert spec.gates == frozenset()
    assert spec.gate_timeout_s is None
    assert spec.max_context_tokens is None
    assert spec.workflow == ()
    assert spec.build_graph is None


@pytest.mark.parametrize(
    ("kind", "parent", "resumable"),
    [("execution", None, True), ("control", None, False), ("internal", "segmentation_agent", False)],
)
def test_resumable_defaults_per_kind(kind, parent, resumable):
    spec = _agent(kind=kind, parent=parent, delegates=[], inputs={}, outputs={})

    assert spec.resumable is resumable
    assert _agent(resumable=False).resumable is False


def test_agent_spec_is_not_a_tool_spec():
    assert not issubclass(AgentSpec, ToolSpec)
    assert not isinstance(_agent(), ToolSpec)


def test_spec_is_immutable_and_copy_replace_revalidates():
    spec = _agent()

    with pytest.raises(AttributeError):
        spec.kind = AgentKind.CONTROL  # type: ignore[misc]
    with pytest.raises(TypeError):
        spec.inputs["masks"] = PortSpec("MaskSet")  # type: ignore[index]

    changed = copy.replace(spec, timeout_s=10800, gates=["segmentation_review"])
    assert (changed.timeout_s, changed.human_in_the_loop) == (10800, True)
    with pytest.raises(ValueError, match="kind="):
        copy.replace(spec, kind="planner")


def test_specs_compare_by_identity():
    first, second = _agent(), _agent()

    assert first != second
    assert len({first, second}) == 2


def test_a_population_style_agent():
    spec = _agent(
        agent_type="study_population_planner_agent",
        inputs={},
        outputs={"cohort": PortSpec("Cohort", extract=lambda r: ExtractedArtifact(ref=r))},
        delegates=[],
        completion_check=_handoff_issues,
        max_completion_retries=2,
        gates=["cohort_approval"],
        ui="population_view",
    )

    assert spec.completion_check is _handoff_issues
    assert spec.max_completion_retries == 2
    assert spec.gates == frozenset({Gate.COHORT_APPROVAL})
    assert spec.human_in_the_loop is True


def test_an_internal_agent():
    spec = _agent(
        agent_type="mask_resolution_agent",
        kind="internal",
        parent="segmentation_agent",
        allowed_tools=["list_models"],
        delegates=(),
        inputs={},
        outputs={},
    )

    assert spec.kind is AgentKind.INTERNAL
    assert spec.parent == "segmentation_agent"
    assert not spec.is_graph_node


@pytest.mark.parametrize(
    ("kind", "graph_node"),
    [("execution", True), ("control", False), ("internal", False)],
)
def test_only_execution_agents_are_graph_nodes(kind, graph_node):
    spec = _agent(kind=kind, inputs={}, outputs={})

    assert spec.is_graph_node is graph_node


# --- system prompt


def test_prompt_for_fixed_text():
    assert _agent().prompt_for(CTX) == "You segment the cohort."


def test_prompt_for_a_function_of_the_context():
    spec = _agent(system_prompt=lambda ctx: f"Session {ctx.session_id}.")

    assert spec.prompt_for(CTX) == "Session s1."


def test_prompt_function_must_return_text():
    spec = _agent(system_prompt=lambda ctx: "  ")

    with pytest.raises(ValueError, match="returned no text"):
        spec.prompt_for(CTX)


# --- allowed tools


def test_fixed_allowlist_ignores_the_registered_tools():
    spec = _agent()

    assert not spec.has_dynamic_tools
    assert spec.tools_for(CTX, [_tool("get_patient", {"main"})]) == (
        "run_segmentation_with_guard",
        "list_models",
    )


def test_allowlist_by_visibility():
    def main_tools(ctx: Any, tools: Sequence[ToolSpec]) -> list[str]:
        return [tool.name for tool in tools if tool.visible_to("main")]

    spec = _agent(
        agent_type="main_agent", kind="control", allowed_tools=main_tools, inputs={}, outputs={}
    )
    tools = [
        _tool("get_patient", {"main", "agent"}),
        _tool("scratchpad_set_status", {"harness"}),
        _tool("get_volume", {"main"}),
    ]

    assert spec.has_dynamic_tools
    assert spec.tools_for(CTX, tools) == ("get_patient", "get_volume")


def test_allowlist_function_result_is_checked():
    spec = _agent(allowed_tools=lambda ctx, tools: ["list_models", "list_models"])

    with pytest.raises(ValueError, match="more than once"):
        spec.tools_for(CTX, [])


# --- malformed fields


@pytest.mark.parametrize(
    ("overrides", "error", "match"),
    [
        ({"agent_type": ""}, ValueError, "agent_type"),
        ({"agent_type": "SegmentationAgent"}, ValueError, "agent_type"),
        ({"agent_type": "segmentation-agent"}, ValueError, "agent_type"),
        ({"agent_type": "a" * 65}, ValueError, "agent_type"),
        ({"description": " "}, ValueError, "description"),
        ({"kind": "planner"}, ValueError, "kind='planner'"),
        ({"version": 0}, ValueError, "version"),
        ({"version": True}, ValueError, "version"),
        ({"system_prompt": ""}, ValueError, "system_prompt"),
        ({"system_prompt": 42}, TypeError, "system_prompt"),
        ({"allowed_tools": "list_models"}, TypeError, "not a string"),
        ({"allowed_tools": ["list models"]}, ValueError, "allowed_tools entry"),
        ({"allowed_tools": ["a", "b", "a"]}, ValueError, r"\['a'\] more than once"),
        ({"delegates": "mask_resolution_agent"}, TypeError, "not a string"),
        ({"delegates": ["MaskAgent"]}, ValueError, "delegates entry"),
        ({"delegates": ["x_agent", "x_agent"]}, ValueError, "more than once"),
        ({"model_profile": ""}, ValueError, "model_profile"),
        ({"inputs": {"cohort": PortSpec("Cohort", param="cohort_id")}}, ValueError, "set param"),
        ({"inputs": {"cohort": PortSpec("Cohort", extract=_masks)}}, ValueError, "set extract"),
        ({"outputs": {"masks": PortSpec("MaskSet", param="masks")}}, ValueError, "set param"),
        ({"outputs": {"masks": "MaskSet"}}, TypeError, "must be a PortSpec"),
        ({"max_tool_iterations": 0}, ValueError, "max_tool_iterations"),
        ({"timeout_s": 0}, ValueError, "timeout_s"),
        ({"resources": {"tpu"}}, ValueError, "resources="),
        ({"resources": "llm"}, TypeError, "not a string"),
        ({"completion_check": "check"}, TypeError, "completion_check"),
        ({"max_completion_retries": -1}, ValueError, "max_completion_retries"),
        ({"output_contract": dict}, TypeError, "output_contract"),
        ({"output_contract": _Proposal(summary="x")}, TypeError, "output_contract"),
        ({"context_builder": "build"}, TypeError, "context_builder"),
        ({"ui": ""}, ValueError, "ui"),
        ({"resumable": "yes"}, TypeError, "resumable"),
        ({"parent": "Segmentation"}, ValueError, "parent"),
        ({"gates": ["manual_review"]}, ValueError, "gates='manual_review'"),
        ({"gates": "cohort_approval"}, TypeError, "not a string"),
        ({"gates": ["tool_approval"]}, ValueError, "implicit gates"),
        ({"gate_timeout_s": 600}, ValueError, "needs at least one gate"),
        ({"gates": ["segmentation_review"], "gate_timeout_s": 0}, ValueError, "gate_timeout_s"),
        ({"gates": ["segmentation_review"], "gate_timeout_s": True}, ValueError, "gate_timeout_s"),
        ({"max_context_tokens": 0}, ValueError, "max_context_tokens"),
        ({"max_context_tokens": 60000.0}, ValueError, "max_context_tokens"),
        ({"max_context_tokens": True}, ValueError, "max_context_tokens"),
        ({"workflow": "pico_analyst"}, TypeError, "not a string"),
        ({"workflow": ["PicoAnalyst"]}, ValueError, "workflow entry"),
        ({"workflow": ["segmentation_agent"], "delegates": []}, ValueError, "agent itself"),
        ({"build_graph": "graph"}, TypeError, "build_graph must be callable"),
    ],
)
def test_malformed_fields_are_refused(overrides, error, match):
    with pytest.raises(error, match=match):
        _agent(**overrides)


# --- gates, context budget, workflow


def test_human_in_the_loop_is_derived_from_the_gates():
    spec = _agent(gates=["segmentation_review"], gate_timeout_s=86400)

    assert spec.gates == frozenset({Gate.SEGMENTATION_REVIEW})
    assert spec.gate_timeout_s == 86400
    assert spec.human_in_the_loop is True
    assert _agent().human_in_the_loop is False
    with pytest.raises(TypeError):
        _agent(human_in_the_loop=True)  # no longer a field: declare the gates instead


def test_max_context_tokens():
    assert _agent(max_context_tokens=60000).max_context_tokens == 60000


def _build_graph(ctx: Any) -> object:
    return object()


def _literature(**overrides: Any) -> AgentSpec:
    fields: dict[str, Any] = {
        "agent_type": "literature_review_agent",
        "delegates": [],
        "allowed_tools": [],
        "inputs": {},
        "outputs": {},
        "workflow": ["pico_analyst", "query_builder", "search_executor"],
        "build_graph": _build_graph,
    }
    fields.update(overrides)
    return _agent(**fields)


def test_a_workflow_agent_keeps_its_steps_in_order():
    spec = _literature()

    assert spec.workflow == ("pico_analyst", "query_builder", "search_executor")
    assert spec.build_graph is _build_graph


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"delegates": ["clinical_target_intent_agent"]}, "use one or the other"),
        ({"build_graph": None}, "workflow and build_graph go together"),
        ({"workflow": []}, "workflow and build_graph go together"),
        ({"workflow": ["pico_analyst", "pico_analyst"]}, "more than once"),
    ],
)
def test_workflow_rules(overrides, match):
    with pytest.raises(ValueError, match=match):
        _literature(**overrides)


def test_required_fields():
    with pytest.raises(TypeError):
        AgentSpec(  # type: ignore[call-arg]
            agent_type="segmentation_agent",
            description="Segments.",
            kind="execution",
        )


def test_output_contract_accepts_a_model_class():
    spec = _agent(
        agent_type="composer", kind="control", output_contract=_Proposal, inputs={}, outputs={}
    )

    assert spec.output_contract is _Proposal
