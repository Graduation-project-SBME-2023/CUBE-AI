"""Dispatch: the one way a tool is called (merged plan, Phase 1.4 step 1).

``Dispatcher.invoke(spec, args, ctx, cancel)`` is used by the Main Agent's
direct calls, by agent engines and by the Node Runner alike, so every call
gets the same treatment:

1. the ``ToolContext`` is set for the call (``current_context()`` works inside);
2. the arguments are validated against the tool's model;
3. the ``before_invoke`` hooks may answer instead of the tool (the Guardrails
   plug in here in Plan 11: a refusal, or an approval request);
4. the call is routed by ``execution`` to a runner (inline now; ``job`` in
   Plan 5 and ``external`` in Plan 14 register theirs);
5. the time budget and the cancel token apply;
6. any exception becomes an ``error_result`` (logged, never sent to the model
   as a traceback);
7. one ``tool_started`` and exactly one ``tool_finished`` or ``error`` event
   are emitted, and after a success ``on_result``'s view events are sent.

A call refused before it starts (bad arguments, a hook's answer) emits no
events: events describe calls that ran.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from ..context import ToolContext, use_context
from ..events import EventType, TextPayload, ToolEvent
from ..manifest import Execution
from ..results import error_result, is_error_result
from ..tools.spec import ToolSpec
from .cancel import CancelToken, ToolCancelled
from .inline import run_inline

logger = logging.getLogger(__name__)

# Runs one validated call: (spec, arguments, context, cancel) -> the tool's result.
type Runner = Callable[
    [ToolSpec, dict[str, Any], ToolContext, CancelToken | None], Awaitable[Any]
]
# Returns None to let the call run, or a result to return instead of running it.
type BeforeInvoke = Callable[[ToolSpec, Mapping[str, Any], ToolContext], Awaitable[Any]]


class InvalidArguments(ValueError):
    """The model sent arguments the tool's schema does not accept."""


@dataclass(frozen=True)
class Dispatcher:
    before_invoke: tuple[BeforeInvoke, ...] = ()
    runners: Mapping[Execution, Runner] = field(
        default_factory=lambda: {Execution.INLINE: run_inline}
    )

    async def invoke(
        self,
        spec: ToolSpec,
        args: Mapping[str, Any],
        ctx: ToolContext,
        cancel: CancelToken | None = None,
    ) -> Any:
        ctx = ctx.for_tool(spec.name)
        with use_context(ctx):
            try:
                kwargs = validate_arguments(spec, args)
                for hook in self.before_invoke:
                    answer = await hook(spec, kwargs, ctx)
                    if answer is not None:
                        return answer
            except InvalidArguments as exc:
                return error_result("invalid_args", f"{spec.name}: {exc}")
            except Exception:  # a hook that cannot decide must not let the call through
                logger.exception("before_invoke failed for tool %s", spec.name)
                return error_result("error", f"{spec.name} could not be checked")
            return await self._run(spec, kwargs, ctx, cancel)

    async def _run(
        self, spec: ToolSpec, kwargs: dict[str, Any], ctx: ToolContext, cancel: CancelToken | None
    ) -> Any:
        runner = self.runners.get(spec.execution)
        if runner is None:
            return error_result(
                "execution_unavailable", f"{spec.execution} tools cannot run here yet"
            )
        if cancel is not None and cancel.cancelled:
            return error_result("cancelled", f"{spec.name} was cancelled")

        budget = spec.timeout_s + (spec.queue_timeout_s or 0)
        started = time.monotonic()
        await _emit(ctx, "tool_started", f"{spec.name} started")
        scope = asyncio.timeout(budget)
        try:
            async with scope:
                result = await _until_cancelled(runner(spec, kwargs, ctx, cancel), cancel)
        except ToolCancelled:
            result = error_result("cancelled", f"{spec.name} was cancelled")
        except asyncio.CancelledError:  # the task running the call was cancelled
            await _emit(ctx, "error", f"{spec.name} was cancelled")
            raise
        except Exception as exc:
            if isinstance(exc, TimeoutError) and scope.expired():
                result = error_result("timeout", f"{spec.name} did not finish within {budget:g} s")
            else:
                logger.exception("tool %s failed", spec.name)
                reason = str(exc) or type(exc).__name__
                result = error_result("error", f"{spec.name} failed: {reason}")

        if is_error_result(result):
            await _emit(ctx, "error", result["error_message"])
            return result
        elapsed = time.monotonic() - started
        await _emit(ctx, "tool_finished", f"{spec.name} finished in {elapsed:.1f} s")
        await _send_views(spec, result, ctx)
        return result


def validate_arguments(spec: ToolSpec, args: Mapping[str, Any]) -> dict[str, Any]:
    """The handler's keyword arguments (without the ToolContext), validated.

    As FastMCP did, a JSON string sent for a non-string argument is parsed
    first (models often send nested objects that way), and nested models are
    passed to the handler as model objects, not dicts.
    """
    if not isinstance(args, Mapping):
        raise InvalidArguments(f"arguments must be an object, not {type(args).__name__}")
    if spec.input_model is None:  # a raw schema: the external runner validates (Plan 14)
        return dict(args)
    try:
        model = spec.input_model.model_validate(_parse_json_strings(spec.input_model, args))
    except ValidationError as exc:
        raise InvalidArguments(_describe(exc)) from exc
    return dict(model)


def _parse_json_strings(model: type[BaseModel], args: Mapping[str, Any]) -> dict[str, Any]:
    parsed = dict(args)
    for name, info in model.model_fields.items():
        value = parsed.get(name)
        if isinstance(value, str) and info.annotation is not str:
            try:
                decoded = json.loads(value)
            except ValueError:
                continue
            if isinstance(decoded, (dict, list)):
                parsed[name] = decoded
    return parsed


def _describe(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, error['loc'])) or 'arguments'}: {error['msg']}"
        for error in exc.errors()
    )


async def _until_cancelled[T](call: Awaitable[T], cancel: CancelToken | None) -> T:
    """Await ``call``, stopping it and raising ``ToolCancelled`` if ``cancel`` fires first."""
    if cancel is None:
        return await call
    task = asyncio.ensure_future(call)
    waiter = asyncio.ensure_future(cancel.wait())
    try:
        await asyncio.wait((task, waiter), return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            return task.result()
        raise ToolCancelled
    finally:
        waiter.cancel()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _emit(ctx: ToolContext, event_type: EventType, message: str) -> None:
    """Best effort: a failing event sink never fails the call."""
    if ctx.event_sink is None:
        return
    try:
        event = ToolEvent.for_context(ctx, event_type, TextPayload(message=message))
        await ctx.event_sink.emit(event)
    except Exception:
        logger.exception("could not emit %s for tool %s", event_type, ctx.tool_name)


async def _send_views(spec: ToolSpec, result: Any, ctx: ToolContext) -> None:
    """Best effort, like events: the tool has already succeeded."""
    if spec.on_result is None or ctx.event_sink is None:
        return
    try:
        for view in await spec.on_result(result, ctx):
            await ctx.event_sink.emit_view(view)
    except Exception:
        logger.exception("on_result failed for tool %s", spec.name)
