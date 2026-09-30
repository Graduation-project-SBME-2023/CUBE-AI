"""Dispatch: the one way a tool is called (merged plan, Phase 1.4 step 1)."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import BaseModel

from agent_system.toolkit.context import NoToolContextError, ToolContext, current_context
from agent_system.toolkit.events import ToolEvent, ViewEvent
from agent_system.toolkit.manifest import Execution
from agent_system.toolkit.results import error_result
from agent_system.toolkit.runtime.cancel import CancelToken
from agent_system.toolkit.runtime.dispatch import Dispatcher, validate_arguments
from agent_system.toolkit.tools.registry import spec_of, tool
from agent_system.toolkit.tools.spec import ToolSpec

pytestmark = pytest.mark.unit

READ = {
    "pack": "test",
    "side_effects": "none",
    "cost": "light",
    "execution": "inline",
    "visibility": {"agent"},
    "read_only": True,
}


class _Sink:
    def __init__(self) -> None:
        self.events: list[ToolEvent] = []
        self.views: list[ViewEvent] = []

    async def emit(self, event: ToolEvent) -> None:
        self.events.append(event)

    async def emit_view(self, event: ViewEvent) -> None:
        self.views.append(event)

    @property
    def types(self) -> list[str]:
        return [event.event_type for event in self.events]


def _ctx(sink: Any = None) -> ToolContext:
    return ToolContext(
        session_id="s1", caller="agent", agent_type="segmentation_agent", event_sink=sink
    )


def _spec(fn: Any, **overrides: Any) -> ToolSpec:
    return spec_of(tool(**(READ | overrides))(fn))  # type: ignore[return-value]


def _invoke(spec: ToolSpec, args: Mapping[str, Any], ctx: ToolContext, **kwargs: Any) -> Any:
    dispatcher = kwargs.pop("dispatcher", Dispatcher())
    return asyncio.run(dispatcher.invoke(spec, args, ctx, **kwargs))


# --- a successful call


async def get_patient(patient_id: str, ctx: ToolContext) -> dict[str, Any]:
    """Read one patient."""
    running = current_context()
    return {
        "patient_id": patient_id,
        "same_context": running is ctx,
        "tool_name": running.tool_name,
        "session": running.session_id,
    }


def test_a_call_runs_with_its_context():
    sink = _Sink()

    result = _invoke(_spec(get_patient), {"patient_id": "p1"}, _ctx(sink))

    assert result == {
        "patient_id": "p1",
        "same_context": True,  # the handler's ctx argument is the running context
        "tool_name": "get_patient",
        "session": "s1",
    }
    with pytest.raises(NoToolContextError):
        current_context()  # and it is gone after the call


def test_a_call_emits_one_start_and_one_finish():
    sink = _Sink()

    _invoke(_spec(get_patient), {"patient_id": "p1"}, _ctx(sink))

    assert sink.types == ["tool_started", "tool_finished"]
    assert {(e.source, e.tool_name, e.session_id) for e in sink.events} == {
        ("segmentation_agent", "get_patient", "s1")
    }


def test_a_call_works_without_an_event_sink():
    assert _invoke(_spec(get_patient), {"patient_id": "p1"}, _ctx())["patient_id"] == "p1"


# --- arguments


class TrainParams(BaseModel):
    fold: int
    method: str = "rf"


async def train(params: TrainParams) -> dict[str, Any]:
    """Train."""
    return {"type": type(params).__name__, "fold": params.fold}


def test_invalid_arguments_are_refused_before_the_call():
    sink = _Sink()

    result = _invoke(_spec(get_patient), {"patient": "p1"}, _ctx(sink))

    assert result == error_result("invalid_args", "get_patient: patient_id: Field required")
    assert sink.events == []


def test_arguments_must_be_an_object():
    result = _invoke(_spec(get_patient), ["p1"], _ctx())  # type: ignore[arg-type]

    assert result["error_code"] == "invalid_args"
    assert "must be an object, not list" in result["error_message"]


def test_nested_models_reach_the_handler_as_models():
    result = _invoke(_spec(train), {"params": {"fold": 5}}, _ctx())

    assert result == {"type": "TrainParams", "fold": 5}


def test_a_json_string_for_an_object_argument_is_parsed():
    assert _invoke(_spec(train), {"params": '{"fold": 3}'}, _ctx())["fold"] == 3


def test_validate_arguments_leaves_raw_schema_tools_to_their_runner():
    spec = copy.replace(_spec(get_patient), input_model=None, context_param=None,
                        input_schema={"type": "object"})  # fmt: skip

    assert validate_arguments(spec, {"anything": 1}) == {"anything": 1}


# --- before_invoke


def test_a_hook_can_answer_instead_of_the_tool():
    sink, calls, seen = _Sink(), [], []

    async def handler(patient_id: str) -> dict[str, Any]:
        """Read."""
        calls.append(patient_id)
        return {}

    async def deny(spec: ToolSpec, args: Mapping[str, Any], ctx: ToolContext) -> Any:
        seen.append((spec.name, dict(args), ctx.tool_name))
        return error_result("permission_denied", "not in plan mode")

    result = _invoke(
        _spec(handler, name="read"), {"patient_id": "p1"}, _ctx(sink),
        dispatcher=Dispatcher(before_invoke=(deny,)),
    )  # fmt: skip

    assert result == error_result("permission_denied", "not in plan mode")
    assert seen == [("read", {"patient_id": "p1"}, "read")]
    assert calls == []
    assert sink.events == []


def test_hooks_that_allow_let_the_call_run():
    async def allow(spec: ToolSpec, args: Mapping[str, Any], ctx: ToolContext) -> None:
        return None

    dispatcher = Dispatcher(before_invoke=(allow, allow))
    result = _invoke(_spec(get_patient), {"patient_id": "p1"}, _ctx(), dispatcher=dispatcher)

    assert result["patient_id"] == "p1"


def test_a_failing_hook_refuses_the_call():
    async def broken(spec: ToolSpec, args: Mapping[str, Any], ctx: ToolContext) -> None:
        raise RuntimeError("guardrail store unreachable")

    result = _invoke(
        _spec(get_patient), {"patient_id": "p1"}, _ctx(),
        dispatcher=Dispatcher(before_invoke=(broken,)),
    )  # fmt: skip

    assert result == error_result("error", "get_patient could not be checked")


# --- failures


async def slow(seconds: float) -> dict[str, Any]:
    """Sleep."""
    await asyncio.sleep(seconds)
    return {"slept": seconds}


def test_a_call_over_its_budget_times_out():
    sink = _Sink()

    result = _invoke(_spec(slow, timeout_s=0.05), {"seconds": 5}, _ctx(sink))

    assert result == error_result("timeout", "slow did not finish within 0.05 s")
    assert sink.types == ["tool_started", "error"]


def test_the_budget_includes_the_queue_time():
    async def run_job(spec: ToolSpec, kwargs: dict, ctx: ToolContext, cancel: Any) -> Any:
        return await spec.handler(**kwargs)

    spec = _spec(slow, timeout_s=0.05, queue_timeout_s=1)
    dispatcher = Dispatcher(runners={Execution.INLINE: run_job})

    assert _invoke(spec, {"seconds": 0.2}, _ctx(), dispatcher=dispatcher) == {"slept": 0.2}


def test_an_exception_becomes_an_error_result():
    sink = _Sink()

    async def broken(patient_id: str) -> dict[str, Any]:
        """Break."""
        raise ConnectionError("application API unreachable")

    result = _invoke(_spec(broken), {"patient_id": "p1"}, _ctx(sink))

    assert result == error_result("error", "broken failed: application API unreachable")
    assert "Traceback" not in result["error_message"]
    assert sink.types == ["tool_started", "error"]


def test_a_tools_own_timeout_error_is_not_a_budget_timeout():
    async def upstream_timeout(patient_id: str) -> dict[str, Any]:
        """Time out upstream."""
        raise TimeoutError

    result = _invoke(_spec(upstream_timeout), {"patient_id": "p1"}, _ctx())

    assert result == error_result("error", "upstream_timeout failed: TimeoutError")


def test_an_error_result_from_the_tool_is_reported_as_an_error():
    sink, views = _Sink(), []

    async def missing(patient_id: str) -> dict[str, Any]:
        """Report missing."""
        return error_result("not_found", f"patient {patient_id} does not exist")

    async def on_result(result: Any, ctx: ToolContext) -> list[ViewEvent]:
        views.append(result)
        return []

    result = _invoke(_spec(missing, on_result=on_result), {"patient_id": "p9"}, _ctx(sink))

    assert result["error_code"] == "not_found"
    assert sink.types == ["tool_started", "error"]
    assert sink.events[-1].content.message == "patient p9 does not exist"
    assert views == []  # on_result runs only after a success


# --- routing


def test_a_tool_whose_runner_is_not_installed_yet():
    spec = _spec(slow, execution="job", resources={"cpu_heavy"}, isolation="process")

    assert _invoke(spec, {"seconds": 0}, _ctx()) == error_result(
        "execution_unavailable", "job tools cannot run here yet"
    )


def test_runners_are_chosen_by_execution():
    received = []

    async def run_job(spec: ToolSpec, kwargs: dict, ctx: ToolContext, cancel: Any) -> Any:
        received.append((spec.name, kwargs, ctx.tool_name, cancel))
        return {"job": "done"}

    spec = _spec(slow, execution="job", resources={"cpu_heavy"}, isolation="process")
    token = CancelToken()
    dispatcher = Dispatcher(runners={Execution.JOB: run_job})

    assert _invoke(spec, {"seconds": 1}, _ctx(), cancel=token, dispatcher=dispatcher) == {
        "job": "done"
    }
    assert received == [("slow", {"seconds": 1.0}, "slow", token)]


# --- cancellation


def test_a_cancel_token_stops_a_running_call():
    sink, stopped = _Sink(), []

    async def long_task(seconds: float) -> dict[str, Any]:
        """Run long."""
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            stopped.append(True)
            raise
        return {}

    async def scenario() -> Any:
        token = CancelToken()
        call = asyncio.create_task(
            Dispatcher().invoke(_spec(long_task), {"seconds": 5}, _ctx(sink), token)
        )
        await asyncio.sleep(0.01)
        token.cancel()
        return await call

    assert asyncio.run(scenario()) == error_result("cancelled", "long_task was cancelled")
    assert stopped == [True]
    assert sink.types == ["tool_started", "error"]


def test_an_already_cancelled_token_does_not_start_the_call():
    sink, token = _Sink(), CancelToken()
    token.cancel()

    result = _invoke(_spec(get_patient), {"patient_id": "p1"}, _ctx(sink), cancel=token)

    assert result == error_result("cancelled", "get_patient was cancelled")
    assert sink.events == []


def test_cancelling_the_task_propagates_and_still_closes_the_call():
    sink = _Sink()

    async def scenario() -> None:
        call = asyncio.create_task(Dispatcher().invoke(_spec(slow), {"seconds": 5}, _ctx(sink)))
        await asyncio.sleep(0.01)
        call.cancel()
        await call

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())
    assert sink.types == ["tool_started", "error"]


# --- on_result and sink failures


def test_on_result_sends_view_events():
    sink, received = _Sink(), []

    async def on_result(result: Any, ctx: ToolContext) -> list[ViewEvent]:
        received.append((result["patient_id"], ctx.tool_name))
        return [ViewEvent("population_output", {"tool": ctx.tool_name, "data": result})]

    _invoke(_spec(get_patient, on_result=on_result), {"patient_id": "p1"}, _ctx(sink))

    assert received == [("p1", "get_patient")]
    assert [(view.type, view.payload["tool"]) for view in sink.views] == [
        ("population_output", "get_patient")
    ]


def test_a_failing_on_result_does_not_fail_the_call():
    async def on_result(result: Any, ctx: ToolContext) -> list[ViewEvent]:
        raise KeyError("frontend_data")

    spec = _spec(get_patient, on_result=on_result)

    assert _invoke(spec, {"patient_id": "p1"}, _ctx(_Sink()))["patient_id"] == "p1"


def test_a_failing_event_sink_does_not_fail_the_call():
    class BrokenSink(_Sink):
        async def emit(self, event: ToolEvent) -> None:
            raise ConnectionError("websocket closed")

    result = _invoke(_spec(get_patient), {"patient_id": "p1"}, _ctx(BrokenSink()))

    assert result["patient_id"] == "p1"


# --- CancelToken


def test_cancel_token():
    async def scenario() -> list[bool]:
        token = CancelToken()
        before = token.cancelled
        waiter = asyncio.create_task(token.wait())
        await asyncio.sleep(0)
        token.cancel()
        await asyncio.wait_for(waiter, 1)
        return [before, token.cancelled]

    assert asyncio.run(scenario()) == [False, True]
