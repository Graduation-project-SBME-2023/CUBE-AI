"""ToolContext: who is calling a tool, for which session, node and run
(merged plan, Phase 1.1 step 5).

Tools read their identity from here, never from environment variables. The
context lives in a ``ContextVar``: dispatch (Phase 1.4) sets it around every
call with ``use_context()``, and tools read it with ``current_context()``.
Each asyncio task gets its own copy, so two sessions never see each other's
context.

A job (Plan 5) runs in another process: the context crosses over as
``to_payload()`` and is rebuilt with ``from_payload()``. The payload is JSON
and leaves out the live objects (``event_sink``, ``session_handle``,
``mailbox``, ``cancel``); the worker attaches its own.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, fields
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .manifest import coerce_enum

if TYPE_CHECKING:
    from .events import EventSink
    from .runtime.cancel import CancelToken


class Caller(StrEnum):
    """Who started the call."""

    MAIN = "main"  # the Main Agent, directly
    AGENT = "agent"  # an agent inside a node (or an internal agent it started)
    NODE = "node"  # the Node Runner, running a Tool Node
    HARNESS = "harness"  # harness code (bookkeeping, guardrails)


class ExecutionMode(StrEnum):
    AUTO = "auto"  # the default: valid work is applied at once (Plan 11.2)
    PLAN = "plan"  # opt-in: work waits for approval


class PrivacyMode(StrEnum):
    STANDARD = "standard"
    PHI_LOCAL = "phi_local"  # patient data may only reach local models


# The live objects; they never enter the payload.
LIVE_FIELDS: frozenset[str] = frozenset({"event_sink", "session_handle", "mailbox", "cancel"})


class NoToolContextError(RuntimeError):
    """A tool asked for its context outside a dispatched call."""


@dataclass(frozen=True, kw_only=True)
class ToolContext:
    """Immutable: derive a changed context with ``copy.replace()`` or the
    ``for_*`` helpers, then set it with ``use_context()``."""

    session_id: str
    caller: Caller
    node_id: str | None = None
    run_id: str | None = None  # the node_runs row of the current node
    attempt_id: str | None = None  # the run_attempts row: the D24 write fence
    agent_type: str | None = None  # the node's agent (or main_agent)
    subagent: str | None = None  # the internal agent run_agent started
    depth: int = 0  # run_agent nesting; capped by run_agent (MAX_AGENT_DEPTH)
    workspace_dir: Path | None = None
    tool_name: str | None = None  # the tool being called
    job_id: str | None = None
    execution_mode: ExecutionMode = ExecutionMode.AUTO
    privacy_mode: PrivacyMode = PrivacyMode.STANDARD

    # --- live objects: excluded from the payload, equality and repr
    event_sink: EventSink | None = field(default=None, compare=False, repr=False)
    session_handle: Any = field(default=None, compare=False, repr=False)
    mailbox: asyncio.Queue[str] | None = field(default=None, compare=False, repr=False)
    # The call's cancel token, set by dispatch: long work can check it or pass it down.
    cancel: CancelToken | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        def _set(name: str, value: Any) -> None:
            object.__setattr__(self, name, value)

        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        _set("caller", coerce_enum(Caller, self.caller, "caller"))
        _set("execution_mode", coerce_enum(ExecutionMode, self.execution_mode, "execution_mode"))
        _set("privacy_mode", coerce_enum(PrivacyMode, self.privacy_mode, "privacy_mode"))
        if isinstance(self.depth, bool) or not isinstance(self.depth, int) or self.depth < 0:
            raise ValueError("depth must be an int >= 0")
        if self.workspace_dir is not None:
            _set("workspace_dir", Path(self.workspace_dir))

    # --- derived contexts

    def for_tool(self, tool_name: str, *, cancel: CancelToken | None = None) -> ToolContext:
        """The context for one tool call made from this context. The call's
        cancel token defaults to the one this context runs under."""
        return copy.replace(self, tool_name=tool_name, cancel=cancel or self.cancel)

    def for_subagent(self, agent_type: str) -> ToolContext:
        """The context of an internal agent started with run_agent: one level deeper."""
        return copy.replace(self, subagent=agent_type, depth=self.depth + 1, tool_name=None)

    # --- crossing a process boundary

    def to_payload(self) -> dict[str, Any]:
        """JSON-ready fields, without the live objects."""
        payload: dict[str, Any] = {}
        for item in fields(self):
            if item.name in LIVE_FIELDS:
                continue
            value = getattr(self, item.name)
            if isinstance(value, StrEnum):
                value = value.value
            elif isinstance(value, Path):
                value = str(value)
            payload[item.name] = value
        return payload

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        event_sink: EventSink | None = None,
        session_handle: Any = None,
        mailbox: asyncio.Queue[str] | None = None,
        cancel: CancelToken | None = None,
    ) -> ToolContext:
        """Rebuild a context from ``to_payload()``, attaching this side's live objects."""
        allowed = {item.name for item in fields(cls)} - LIVE_FIELDS
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(f"unknown ToolContext payload fields: {unknown}")
        return cls(
            **payload,
            event_sink=event_sink,
            session_handle=session_handle,
            mailbox=mailbox,
            cancel=cancel,
        )


_current: ContextVar[ToolContext] = ContextVar("cubeai_tool_context")


def current_context() -> ToolContext:
    """The context of the running call. Raises outside a dispatched call."""
    try:
        return _current.get()
    except LookupError:
        raise NoToolContextError(
            "no ToolContext is set; tools must be called through dispatch"
        ) from None


@contextmanager
def use_context(ctx: ToolContext) -> Iterator[ToolContext]:
    """Set ``ctx`` for the enclosed block and restore the previous one after."""
    if not isinstance(ctx, ToolContext):
        raise TypeError(f"use_context needs a ToolContext, not {ctx!r}")
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
