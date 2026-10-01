"""ToolContext, its ContextVar and its payload (merged plan, Phase 1.1 step 5)."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from agent_system.toolkit.context import (
    Caller,
    ExecutionMode,
    NoToolContextError,
    PrivacyMode,
    ToolContext,
    current_context,
    use_context,
)

pytestmark = pytest.mark.unit


class _Sink:
    async def emit(self, event: Any) -> None: ...

    async def emit_view(self, event: Any) -> None: ...


def _ctx(**overrides: Any) -> ToolContext:
    fields: dict[str, Any] = {
        "session_id": "5f0c1c3e-6f7e-4d7a-9c55-0d8f7c0e2a11",
        "caller": "agent",
        "agent_run_id": "run-1",
        "node_id": "node-1",
        "graph_version": 3,
        "agent_type": "segmentation_agent",
        "workspace_dir": "/storage/hypotheses/workspace_x",
    }
    fields.update(overrides)
    return ToolContext(**fields)


def test_defaults_and_normalisation():
    ctx = ToolContext(session_id="s1", caller="main")

    assert ctx.caller is Caller.MAIN
    assert ctx.execution_mode is ExecutionMode.AUTO
    assert ctx.privacy_mode is PrivacyMode.STANDARD
    assert ctx.depth == 0
    assert ctx.agent_run_id is ctx.node_id is ctx.step_id is ctx.job_id is None
    assert ctx.event_sink is ctx.session_handle is ctx.mailbox is None
    assert _ctx().workspace_dir == Path("/storage/hypotheses/workspace_x")


def test_context_is_immutable():
    with pytest.raises(AttributeError):
        _ctx().session_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"session_id": ""}, "session_id"),
        ({"session_id": None}, "session_id"),
        ({"caller": "planner"}, "caller='planner'"),
        ({"execution_mode": "manual"}, "execution_mode="),
        ({"privacy_mode": "private"}, "privacy_mode="),
        ({"depth": -1}, "depth"),
        ({"depth": True}, "depth"),
        ({"graph_version": -1}, "graph_version"),
        ({"graph_version": "3"}, "graph_version"),
    ],
)
def test_malformed_fields_are_refused(overrides, match):
    with pytest.raises(ValueError, match=match):
        _ctx(**overrides)


def test_caller_is_required():
    with pytest.raises(TypeError):
        ToolContext(session_id="s1")  # type: ignore[call-arg]


# --- derived contexts


def test_for_tool_sets_only_the_tool_name():
    ctx = _ctx(event_sink=_Sink())

    child = ctx.for_tool("list_models")

    assert child.tool_name == "list_models"
    assert child.agent_type == "segmentation_agent"
    assert child.event_sink is ctx.event_sink
    assert ctx.tool_name is None


def test_for_subagent_goes_one_level_deeper():
    ctx = _ctx(tool_name="run_agent", depth=0)

    child = ctx.for_subagent("mask_resolution_agent")

    assert (child.subagent, child.depth, child.tool_name) == ("mask_resolution_agent", 1, None)
    assert child.agent_type == "segmentation_agent"  # still the node's agent
    assert child.for_subagent("clinical_target_intent_agent").depth == 2


# --- payload


def test_payload_is_json_and_leaves_out_live_objects():
    ctx = _ctx(
        caller="node",
        execution_mode="plan",
        privacy_mode="phi_local",
        job_id="job-1",
        event_sink=_Sink(),
        session_handle=object(),
        mailbox=asyncio.Queue(),
    )

    payload = ctx.to_payload()

    assert json.loads(json.dumps(payload)) == payload
    assert not {"event_sink", "session_handle", "mailbox"} & payload.keys()
    assert payload["caller"] == "node"
    assert payload["execution_mode"] == "plan"
    assert payload["privacy_mode"] == "phi_local"
    assert payload["workspace_dir"] == str(Path("/storage/hypotheses/workspace_x"))


def test_payload_round_trip_attaches_this_sides_live_objects():
    ctx = _ctx(job_id="job-1", event_sink=_Sink())
    sink = _Sink()

    rebuilt = ToolContext.from_payload(ctx.to_payload(), event_sink=sink)

    assert rebuilt == ctx  # live objects do not take part in equality
    assert rebuilt.event_sink is sink
    assert rebuilt.session_handle is None


def test_from_payload_refuses_unknown_fields():
    payload = _ctx().to_payload() | {"agent_id": "legacy"}

    with pytest.raises(ValueError, match=r"unknown ToolContext payload fields: \['agent_id'\]"):
        ToolContext.from_payload(payload)


def test_from_payload_cannot_smuggle_live_objects():
    payload = _ctx().to_payload() | {"event_sink": "x"}

    with pytest.raises(ValueError, match="event_sink"):
        ToolContext.from_payload(payload)


def test_from_payload_needs_the_required_fields():
    with pytest.raises(TypeError):
        ToolContext.from_payload({"session_id": "s1"})


# --- ContextVar


def test_current_context_outside_a_call_raises():
    with pytest.raises(NoToolContextError, match="through dispatch"):
        current_context()


def test_use_context_sets_and_restores():
    outer, inner = _ctx(), _ctx(tool_name="list_models")

    with use_context(outer):
        assert current_context() is outer
        with use_context(inner):
            assert current_context() is inner
        assert current_context() is outer
    with pytest.raises(NoToolContextError):
        current_context()


def test_use_context_restores_after_an_error():
    with pytest.raises(RuntimeError, match="boom"):
        with use_context(_ctx()):
            raise RuntimeError("boom")

    with pytest.raises(NoToolContextError):
        current_context()


def test_use_context_needs_a_tool_context():
    with pytest.raises(TypeError):
        with use_context({"session_id": "s1"}):  # type: ignore[arg-type]
            pass


async def _observe(ctx: ToolContext, gate: asyncio.Event, seen: list[str]) -> None:
    with use_context(ctx):
        await gate.wait()  # both tasks are inside their block before either reads
        seen.append(current_context().session_id)


def test_two_sessions_never_see_each_others_context():
    async def scenario() -> list[str]:
        gate, seen = asyncio.Event(), []
        tasks = [
            asyncio.create_task(_observe(_ctx(session_id=sid), gate, seen))
            for sid in ("session-a", "session-b")
        ]
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(*tasks)
        return seen

    assert sorted(asyncio.run(scenario())) == ["session-a", "session-b"]


def test_copy_replace_revalidates():
    with pytest.raises(ValueError, match="depth"):
        copy.replace(_ctx(), depth=-1)
