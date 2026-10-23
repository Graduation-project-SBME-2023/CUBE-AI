"""The agent engine contract (merged plan, Phase 1.4 step 3).

An ``AgentEngine`` runs one ``AgentSpec`` on one task and returns an
``AgentResult``. Nothing outside ``toolkit/engines/`` knows how: the
Claude Agent SDK engine arrives in Plan 9, and ``FakeAgentEngine`` stands in for it
in tests. Every tool call an engine makes goes through dispatch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
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
    caller. A run that did not complete says why in ``error_code``
    (``error``, ``timeout``, ``cancelled``, ``max_iterations``, ...)."""

    status: AgentStatus
    output: Any = None
    summary: str = ""
    usage: Usage = field(default_factory=Usage)
    error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", coerce_enum(AgentStatus, self.status, "status"))
        completed = self.status is AgentStatus.COMPLETED
        if completed and self.error_code is not None:
            raise ValueError("a completed run has no error_code")
        if not completed and not self.error_code:
            raise ValueError(f"a {self.status} run needs an error_code")


@runtime_checkable
class AgentEngine(Protocol):
    async def run(
        self,
        spec: AgentSpec,
        task: str,
        inputs: Mapping[str, Any],
        ctx: ToolContext,
        cancel: CancelToken | None = None,
    ) -> AgentResult: ...


type Script = AgentResult | Callable[..., Awaitable[AgentResult]]


@dataclass(frozen=True)
class EngineCall:
    spec: AgentSpec
    task: str
    inputs: Mapping[str, Any]
    ctx: ToolContext
    cancel: CancelToken | None


class FakeAgentEngine:
    """An engine for tests. Each agent type can be scripted with a fixed
    ``AgentResult`` or an async function taking ``run()``'s arguments; an
    unscripted agent completes with an empty output. Every call is recorded."""

    def __init__(self, scripts: Mapping[str, Script] | None = None) -> None:
        self.scripts = dict(scripts or {})
        self.calls: list[EngineCall] = []

    async def run(
        self,
        spec: AgentSpec,
        task: str,
        inputs: Mapping[str, Any],
        ctx: ToolContext,
        cancel: CancelToken | None = None,
    ) -> AgentResult:
        self.calls.append(EngineCall(spec, task, inputs, ctx, cancel))
        script = self.scripts.get(spec.name)
        if script is None:
            return AgentResult(status=AgentStatus.COMPLETED, summary=f"{spec.name} done")
        if isinstance(script, AgentResult):
            return script
        return await script(spec, task, inputs, ctx, cancel)
