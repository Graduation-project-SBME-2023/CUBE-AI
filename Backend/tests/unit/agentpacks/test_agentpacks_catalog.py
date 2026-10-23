"""Every agent in agentpacks/, checked together (merged plan, Plan 3 and the
Definition of Ready for agents, items 1-3).

These tests run over whatever agents exist, so a new agent.md is checked the
moment it is added.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_system.toolkit import catalog as catalog_module
from agent_system.toolkit.agents.spec import IMPLICIT_TOOLS, MAIN_AGENT, AgentKind, AgentSpec
from agent_system.toolkit.manifest import Visibility

pytestmark = pytest.mark.unit

AGENTPACKS_ROOT = Path(catalog_module.__file__).resolve().parents[1] / "agentpacks"
AGENT_FILES = sorted(AGENTPACKS_ROOT.rglob("agent.md"))

# Instructions the old prompts carried that the new runtime owns or retired: the
# Node Runner registers the run and sets its status; upstream data arrives as
# "Node inputs", not by reading another agent's scratchpad by name (L14); the
# SDK keeps the session and runs the subagents (Phases 0.2 and 3.0).
RETIRED_INSTRUCTIONS = {
    "scratchpad_register_agent": "the Node Runner registers the run",
    "scratchpad_set_status": "the Node Runner sets the run's status",
    "read_agent_task_context": "the task arrives in the first message",
    "scratchpad_read_agent": "upstream data arrives as Node inputs",
    "scratchpad_read_report": "upstream data arrives as Node inputs",
    "read_agent_output": "upstream data arrives as Node inputs",
    "read_all_agent_outputs": "upstream data arrives as Node inputs",
    "run_agent": "internal agents are SDK subagents",
    "LangGraph": "the engine is the Claude Agent SDK",
    "StateGraph": "the engine is the Claude Agent SDK",
    "checkpoint": "a resumable node keeps a saved SDK session",
    "agent_id=": "tools read the agent's identity from the context",
    "result_agent\"": "the result reporter's id is result_reporter_agent",
}
TEMPLATE_PLACEHOLDER = re.compile(r"\{(workspace_folder|session_id|run_id|node_id)\}")


@pytest.fixture(scope="module")
def catalog():
    built = catalog_module.build()
    built.validate()
    return built


def _agents(catalog) -> list[AgentSpec]:
    return [catalog.agents.get(path.parent.name) for path in AGENT_FILES]


def test_every_agent_file_is_in_the_catalog(catalog):
    assert AGENT_FILES, "agentpacks/ holds no agent.md"
    assert {path.parent.name for path in AGENT_FILES} <= catalog.agents.names()


def test_the_agent_counts(catalog):
    """Phase 3.5, Done when: 1 control, 6 execution, 9 internal agents (the
    Main's 2, segmentation's 2, literature's 5)."""
    kinds = [catalog.agents.get(path.parent.name).kind for path in AGENT_FILES]
    assert len(kinds) == 16
    assert [kinds.count(kind) for kind in AgentKind] == [1, 6, 9]
    parents = [spec.parent for spec in _agents(catalog) if spec.parent]
    assert {parent: parents.count(parent) for parent in parents} == {
        MAIN_AGENT: 2, "segmentation_agent": 2, "literature_review_agent": 5,
    }  # fmt: skip


def test_the_pack_directory_matches_the_kind(catalog):
    """The Main and its two subagents are core/; everything else is research/."""
    for path in AGENT_FILES:
        spec = catalog.agents.get(path.parent.name)
        pack = path.parent.parent.name
        core = spec.kind is AgentKind.CONTROL or spec.parent == MAIN_AGENT
        assert pack == ("core" if core else "research"), f"{spec.name} ({spec.kind}) is in {pack}/"


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.parent.name)
def test_no_prompt_keeps_a_retired_instruction(path):
    prompt = path.read_text(encoding="utf-8")

    for text, reason in RETIRED_INSTRUCTIONS.items():
        assert text not in prompt, f"{path.parent.name}: drop {text!r}: {reason}"
    assert not TEMPLATE_PLACEHOLDER.search(prompt), "prompts are fixed text, not templates"


def test_a_prompt_names_only_the_tools_its_agent_may_use(catalog):
    tool_names = catalog.tools.names()
    for spec in _agents(catalog):
        if spec.has_dynamic_tools:
            continue
        named = set(re.findall(r"`([a-z][a-z0-9_]+)`", spec.system_prompt)) & tool_names
        named -= IMPLICIT_TOOLS  # ask_user: given to the agents that own a form, never listed
        assert named <= set(spec.allowed_tools), (
            f"{spec.name} prompt names tools it may not use: {sorted(named - set(spec.allowed_tools))}"
        )


def test_no_agent_may_use_a_harness_tool(catalog):
    harness = {tool.name for tool in catalog.tools if tool.visibility == {Visibility.HARNESS}}
    for spec in _agents(catalog):
        if not spec.has_dynamic_tools:
            assert not harness & set(spec.allowed_tools), spec.name


def test_execution_agents_declare_their_outputs(catalog):
    for spec in _agents(catalog):
        if spec.kind is AgentKind.EXECUTION:
            assert spec.outputs, f"{spec.name} produces nothing a later node could use"


def test_every_execution_agent_asks_only_when_told_and_shows_its_steps(catalog):
    """Phase 3.2's prompt rules: ask only when the node instructions say the user
    wants it (D28), and record the main steps with TaskCreate / TaskUpdate."""
    for spec in _agents(catalog):
        if spec.kind is AgentKind.EXECUTION:
            prompt = spec.system_prompt
            assert "node instructions say" in prompt, spec.name
            assert "AskUserQuestion" in prompt, spec.name
            assert "`TaskCreate`" in prompt and "`TaskUpdate`" in prompt, spec.name


def test_only_the_agents_that_own_a_form_mention_ask_user(catalog):
    owners = {"study_population_planner_agent", "segmentation_agent"}
    for spec in _agents(catalog):
        if not callable(spec.system_prompt):
            assert ("`ask_user`" in spec.system_prompt) is (spec.name in owners), spec.name


def test_the_mains_subagents_never_apply_anything(catalog):
    for name in ("composer", "recovery_agent"):
        spec = catalog.agents.get(name)
        assert spec.has_dynamic_tools
        offered = [SimpleNamespace(name=n) for n in ("graph_apply", "execute", "graph_propose")]
        assert spec.tools_for(None, offered) == ("graph_propose",)


def test_an_output_feeding_other_nodes_is_checked(catalog):
    """Definition of Ready, agents item 4: an execution agent whose output feeds
    other nodes finishes only through a completion check or an output contract."""
    for spec in _agents(catalog):
        if spec.kind is AgentKind.EXECUTION:
            assert spec.completion_check or spec.output_contract, spec.name
            if spec.completion_check:
                assert spec.max_completion_retries >= 1, spec.name
