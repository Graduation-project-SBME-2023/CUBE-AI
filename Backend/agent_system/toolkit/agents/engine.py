"""The agent engine contract (merged plan, Phase 1.4 step 3; Phase 9.3).

An ``AgentEngine`` runs one ``AgentSpec`` on one task and returns an
``AgentResult``. Nothing outside ``toolkit/engines/`` knows how: the
Claude Agent SDK engine arrives in Plan 9, and ``FakeAgentEngine`` stands in for it
in tests. Every tool call an engine makes goes through dispatch.

A run can stop without finishing: it is ``SUSPENDED`` when the agent asks the
user, when a tool call needs an approval, or when the session pauses. Nothing
waits in memory; the Node Runner records the suspension (T10) and later calls
``run()`` again with a ``Resume``. The Main Agent does not run node by node: it
keeps one long-lived session, opened with ``open_main()`` and driven with
``turn()``.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..manifest import coerce_enum
from .spec import AgentSpec

if TYPE_CHECKING:
    from ..context import ToolContext
    from ..runtime.cancel import CancelToken


class AgentStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUSPENDED = "suspended"  # stopped at a deferred call or a pause; resumed with a Resume


class SuspensionKind(StrEnum):
    ASK_USER = "ask_user"  # the agent asked the user (ask_user or AskUserQuestion)
    TOOL_APPROVAL = "tool_approval"  # a tool call needs the user's approval (Phase 11.3)
    PAUSE = "pause"  # the session paused; the run stopped at a tool boundary (Phase 10.6)


@dataclass(frozen=True, kw_only=True)
class Suspension:
    """Why a run stopped and what it waits for. ``tool_use_id`` names the
    deferred call (none for a pause); ``payload`` holds what the user is
    shown: the question or form, or the tool call to approve (refs and
    summaries only)."""

    kind: SuspensionKind
    tool_use_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", coerce_enum(SuspensionKind, self.kind, "kind"))
        deferred = self.kind is not SuspensionKind.PAUSE
        if deferred and not self.tool_use_id:
            raise ValueError(f"a {self.kind} suspension needs the deferred call's tool_use_id")
        if not deferred and self.tool_use_id is not None:
            raise ValueError("a pause has no deferred call")


@dataclass(frozen=True)
class Usage:
    """Model tokens spent by one run (``run_attempts.usage``, Plan 6)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
        )


@dataclass(frozen=True, kw_only=True)
class AgentResult:
    """``output`` is the agent's final structured output (validated against its
    ``output_contract`` by whoever reads it), ``summary`` a short text for the
    caller. A run that failed or was cancelled says why in ``error_code``
    (``error``, ``timeout``, ``cancelled``, ``max_iterations``, ...); a
    suspended run says what it waits for in ``suspension`` instead."""

    status: AgentStatus
    output: Any = None
    summary: str = ""
    usage: Usage = field(default_factory=Usage)
    error_code: str | None = None
    suspension: Suspension | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", coerce_enum(AgentStatus, self.status, "status"))
        ended = self.status in (AgentStatus.FAILED, AgentStatus.CANCELLED)
        if ended and not self.error_code:
            raise ValueError(f"a {self.status} run needs an error_code")
        if not ended and self.error_code is not None:
            raise ValueError(f"a {self.status} run has no error_code")
        suspended = self.status is AgentStatus.SUSPENDED
        if suspended and self.suspension is None:
            raise ValueError("a suspended run needs a suspension")
        if not suspended and self.suspension is not None:
            raise ValueError(f"a {self.status} run has no suspension")


@dataclass(frozen=True)
class Resume:
    """How a suspended or interrupted run continues. ``Resume(value)`` answers
    the deferred call (an ``ask_user`` answer, or a tool-approval decision);
    ``Resume.continue_()`` continues a paused or interrupted run from its
    saved session, with nothing to answer."""

    value: Any = None
    answers_call: bool = True

    @classmethod
    def continue_(cls) -> Resume:
        return cls(answers_call=False)

    def __post_init__(self) -> None:
        if not self.answers_call and self.value is not None:
            raise ValueError("Resume.continue_() carries no value")


@dataclass
class EngineHooks:
    """The live session state an engine reads while a run goes on
    (``ToolContext.engine_hooks``, never in the payload): ``pause`` is set when
    the session is pausing, so the run stops at its next tool boundary, and
    ``mailbox`` holds messages for the running agent (Phase 12.2
    ``node_message``)."""

    pause: asyncio.Event = field(default_factory=asyncio.Event)
    mailbox: asyncio.Queue[str] = field(default_factory=asyncio.Queue)


@runtime_checkable
class MainSession(Protocol):
    """The Main Agent's long-lived session (one per CUBE.AI session, D21).
    ``resumed`` is true when ``open_main()`` found a saved session to continue."""

    spec: AgentSpec
    ctx: ToolContext
    session_id: str
    resumed: bool

    async def close(self) -> None: ...


@runtime_checkable
class AgentEngine(Protocol):
    async def run(
        self,
        spec: AgentSpec,
        task: str,
        inputs: Mapping[str, Any],
        ctx: ToolContext,
        cancel: CancelToken | None = None,
        resume: Resume | None = None,
    ) -> AgentResult: ...

    async def open_main(self, spec: AgentSpec, ctx: ToolContext) -> MainSession: ...

    async def turn(
        self, session: MainSession, message: str, cancel: CancelToken | None = None
    ) -> AgentResult: ...


@dataclass(frozen=True)
class EngineCall:
    spec: AgentSpec
    task: str
    inputs: Mapping[str, Any]
    ctx: ToolContext
    cancel: CancelToken | None
    resume: Resume | None = None


@dataclass(frozen=True)
class TurnCall:
    session: MainSession
    message: str
    cancel: CancelToken | None


# A function script takes run()'s arguments (spec, task, inputs, ctx, cancel), and
# `resume` as a keyword when it declares one; a turn script takes turn()'s.
type Script = AgentResult | Sequence[AgentResult] | Callable[..., Awaitable[AgentResult]]
type TurnScript = AgentResult | Sequence[AgentResult] | Callable[..., Awaitable[AgentResult]]


@dataclass
class FakeMainSession:
    spec: AgentSpec
    ctx: ToolContext
    session_id: str
    resumed: bool = False
    closed: bool = False

    async def close(self) -> None:
        self.closed = True


class FakeAgentEngine:
    """An engine for tests. Each agent type can be scripted with a fixed
    ``AgentResult``, a sequence of results returned one per call (for example
    a suspension, then the completion after the resume), or an async function
    taking ``run()``'s arguments (and ``resume=`` if it declares it). An
    unscripted agent completes with an empty output. ``turns`` scripts the
    Main's turns the same way, by agent type, with ``turn()``'s arguments.
    Every call is recorded, the ``resume`` included."""

    def __init__(
        self,
        scripts: Mapping[str, Script] | None = None,
        *,
        turns: Mapping[str, TurnScript] | None = None,
    ) -> None:
        self.scripts = dict(scripts or {})
        self.turn_scripts = dict(turns or {})
        self.calls: list[EngineCall] = []
        self.turn_calls: list[TurnCall] = []
        self.main_sessions: dict[str, FakeMainSession] = {}
        self._positions: dict[tuple[str, str], int] = {}

    async def run(
        self,
        spec: AgentSpec,
        task: str,
        inputs: Mapping[str, Any],
        ctx: ToolContext,
        cancel: CancelToken | None = None,
        resume: Resume | None = None,
    ) -> AgentResult:
        call = EngineCall(spec, task, inputs, ctx, cancel, resume)
        self.calls.append(call)
        script = self.scripts.get(spec.name)
        if script is None:
            return AgentResult(status=AgentStatus.COMPLETED, summary=f"{spec.name} done")
        extra = {"resume": resume} if callable(script) and _takes_resume(script) else {}
        return await self._play("run", spec.name, script, (spec, task, inputs, ctx, cancel), extra)

    async def open_main(self, spec: AgentSpec, ctx: ToolContext) -> FakeMainSession:
        """Like the real engine, a second open of the same CUBE.AI session
        continues the saved one instead of starting fresh."""
        resumed = ctx.session_id in self.main_sessions
        session = FakeMainSession(spec=spec, ctx=ctx, session_id=ctx.session_id, resumed=resumed)
        self.main_sessions[ctx.session_id] = session
        return session

    async def turn(
        self, session: MainSession, message: str, cancel: CancelToken | None = None
    ) -> AgentResult:
        call = TurnCall(session, message, cancel)
        self.turn_calls.append(call)
        name = session.spec.name
        script = self.turn_scripts.get(name)
        if script is None:
            return AgentResult(status=AgentStatus.COMPLETED, summary=f"{name} replied")
        return await self._play("turn", name, script, (session, message, cancel), {})

    async def _play(
        self, kind: str, name: str, script: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> AgentResult:
        if isinstance(script, AgentResult):
            return script
        if isinstance(script, Sequence):
            position = self._positions.get((kind, name), 0)
            if position >= len(script):
                raise RuntimeError(f"the {kind} script for {name} has no result left")
            self._positions[(kind, name)] = position + 1
            return script[position]
        return await script(*args, **kwargs)


def _takes_resume(script: Callable[..., Any]) -> bool:
    parameters = inspect.signature(script).parameters.values()
    return any(p.name == "resume" or p.kind is p.VAR_KEYWORD for p in parameters)
