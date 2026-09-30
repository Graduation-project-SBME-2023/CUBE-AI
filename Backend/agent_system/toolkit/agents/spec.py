"""AgentSpec: the definition of one LLM agent (merged plan, Phase 1.1 step 4).

An agent is a definition, never a tool: ``AgentSpec`` is a separate class and
does not inherit from ``ToolSpec``. Agents are reached as graph nodes
(``execution``), as the control loop (``control``), or through ``run_agent``
from their parent (``internal``). Specs are normally built from an ``agent.md``
file by the loader (Plan 3); nothing here reads files.

Like ``ToolSpec``, this module only checks each field on its own. The rules
that need other specs (unknown or non-internal delegates, delegation cycles,
unknown tool names, ports on control agents, internal agents without a parent)
belong to ``catalog.validate()`` (Phase 1.3).

"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..manifest import TOOL_NAME_PATTERN, Resource, coerce_enum, coerce_enum_set
from ..ports import PortSpec, freeze_ports

if TYPE_CHECKING:
    from ..context import ToolContext
    from ..tools.spec import ToolSpec


class AgentKind(StrEnum):
    CONTROL = "control"  # main_agent, composer, recovery_agent
    EXECUTION = "execution"  # can be a graph node
    INTERNAL = "internal"  # reached only through run_agent, from its parent


# Agent ids are snake_case (study_population_planner_agent, pico_analyst, ...).
# They share one namespace with tool names, so they also satisfy TOOL_NAME_PATTERN.
AGENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

DEFAULT_MODEL_PROFILE = "agent_default"
DEFAULT_MAX_TOOL_ITERATIONS = 10
DEFAULT_TIMEOUT_S = 3600.0

# The system prompt when it depends on the run rather than being fixed text.
type SystemPromptFn = Callable[[ToolContext], str]
# Picks the allowlist at run time from the registered tools, e.g. "every tool
# with main visibility" for the control agents, or a conditional tool.
type AllowedToolsFn = Callable[[ToolContext, Sequence[ToolSpec]], Iterable[str]]
# Returns what is still missing before the agent may finish; empty = complete.
# The engine feeds the issues back and retries up to max_completion_retries times.
type CompletionCheck = Callable[[ToolContext], Awaitable[Sequence[str]]]
# Builds the agent's first message from its task and its node inputs, keyed by port.
type ContextBuilder = Callable[[str, Mapping[str, Any], ToolContext], str]


def _check_agent_name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not AGENT_TYPE_PATTERN.match(value):
        raise ValueError(f"{what} {value!r} must match {AGENT_TYPE_PATTERN.pattern}")
    return value


def _names(values: Any, what: str, pattern: re.Pattern[str]) -> tuple[str, ...]:
    """A list of unique names, order kept (it is the order shown to the model)."""
    if isinstance(values, (str, bytes)):
        # A bare string would otherwise be split into characters.
        raise TypeError(f"{what} must be a collection of names, not a string: {values!r}")
    names = tuple(values)
    for name in names:
        if not isinstance(name, str) or not pattern.match(name):
            raise ValueError(f"{what} entry {name!r} must match {pattern.pattern}")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"{what} lists {duplicates} more than once")
    return names


@dataclass(frozen=True, eq=False, kw_only=True)
class AgentSpec:
    """Everything the engine, the Executor and the catalog need to know about
    one agent. Instances are immutable and compared by identity: the registry
    holds exactly one spec per ``agent_type``.

    ``system_prompt`` and ``allowed_tools`` are either fixed values or functions
    of the context; use ``prompt_for()`` / ``tools_for()`` rather than reading
    them directly. ``copy.replace(spec, ...)`` re-runs every check.
    """

    # --- identity
    agent_type: str
    description: str
    kind: AgentKind
    version: int = 1

    # --- behaviour
    system_prompt: str | SystemPromptFn
    allowed_tools: tuple[str, ...] | AllowedToolsFn = ()
    delegates: tuple[str, ...] = ()  # internal agents it may start with run_agent
    model_profile: str = DEFAULT_MODEL_PROFILE

    # --- ports (execution agents only; see toolkit/ports.py)
    inputs: Mapping[str, PortSpec] = field(default_factory=dict)
    outputs: Mapping[str, PortSpec] = field(default_factory=dict)

    # --- budgets
    max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS
    timeout_s: float = DEFAULT_TIMEOUT_S
    resources: frozenset[Resource] = frozenset({Resource.LLM})

    # --- completion rules
    completion_check: CompletionCheck | None = None
    max_completion_retries: int = 0
    output_contract: type[BaseModel] | None = None

    # --- first message (Plan 12)
    context_builder: ContextBuilder | None = None

    # --- UI and ownership
    ui: str | None = None  # view type for the node, e.g. "segmentation_view"
    human_in_the_loop: bool = False  # user gates inside the node (cohort approval, review)
    parent: str | None = None  # the owner of an internal agent

    def __post_init__(self) -> None:
        # Frozen dataclass: normalised values go through object.__setattr__.
        def _set(name: str, value: Any) -> None:
            object.__setattr__(self, name, value)

        _check_agent_name(self.agent_type, "agent_type")
        name = self.agent_type
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError(f"agent {name!r}: description must not be empty")
        _set("kind", coerce_enum(AgentKind, self.kind, "kind"))
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError(f"agent {name!r}: version must be an int >= 1")

        if isinstance(self.system_prompt, str):
            if not self.system_prompt.strip():
                raise ValueError(f"agent {name!r}: system_prompt must not be empty")
        elif not callable(self.system_prompt):
            raise TypeError(f"agent {name!r}: system_prompt must be text or a function")

        if not callable(self.allowed_tools):
            _set(
                "allowed_tools",
                _names(self.allowed_tools, f"agent {name!r}: allowed_tools", TOOL_NAME_PATTERN),
            )
        _set(
            "delegates",
            _names(self.delegates, f"agent {name!r}: delegates", AGENT_TYPE_PATTERN),
        )
        if not isinstance(self.model_profile, str) or not self.model_profile.strip():
            raise ValueError(f"agent {name!r}: model_profile must not be empty")

        # Agents get their inputs rendered as "Node inputs", never as handler
        # arguments, so no input port names a param.
        _set("inputs", freeze_ports(self.inputs, "inputs", forbidden=("param", "extract")))
        _set("outputs", freeze_ports(self.outputs, "outputs", forbidden=("param",)))

        if isinstance(self.max_tool_iterations, bool) or self.max_tool_iterations < 1:
            raise ValueError(f"agent {name!r}: max_tool_iterations must be >= 1")
        if not self.timeout_s > 0:
            raise ValueError(f"agent {name!r}: timeout_s must be > 0")
        _set("resources", coerce_enum_set(Resource, self.resources, "resources"))

        if self.completion_check is not None and not callable(self.completion_check):
            raise TypeError(f"agent {name!r}: completion_check must be callable")
        if isinstance(self.max_completion_retries, bool) or self.max_completion_retries < 0:
            raise ValueError(f"agent {name!r}: max_completion_retries must be >= 0")
        if self.output_contract is not None and not (
            isinstance(self.output_contract, type) and issubclass(self.output_contract, BaseModel)
        ):
            raise TypeError(f"agent {name!r}: output_contract must be a pydantic model class")
        if self.context_builder is not None and not callable(self.context_builder):
            raise TypeError(f"agent {name!r}: context_builder must be callable")

        if self.ui is not None and (not isinstance(self.ui, str) or not self.ui.strip()):
            raise ValueError(f"agent {name!r}: ui must be a non-empty string or None")
        if not isinstance(self.human_in_the_loop, bool):
            raise TypeError(f"agent {name!r}: human_in_the_loop must be a bool")
        if self.parent is not None:
            _check_agent_name(self.parent, f"agent {name!r}: parent")

    # --- derived fields

    @property
    def is_graph_node(self) -> bool:
        """Only execution agents can be submitted as graph nodes."""
        return self.kind is AgentKind.EXECUTION

    @property
    def has_dynamic_tools(self) -> bool:
        """True when the allowlist is chosen at run time (no fixed names to validate)."""
        return callable(self.allowed_tools)

    def prompt_for(self, ctx: ToolContext) -> str:
        """The system prompt for this run."""
        if isinstance(self.system_prompt, str):
            return self.system_prompt
        prompt = self.system_prompt(ctx)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"agent {self.agent_type!r}: system_prompt function returned no text")
        return prompt

    def tools_for(self, ctx: ToolContext, tools: Sequence[ToolSpec]) -> tuple[str, ...]:
        """The allowlist for this run. ``tools`` are the registered tools a
        function-valued allowlist chooses from; a fixed list ignores them."""
        if not callable(self.allowed_tools):
            return self.allowed_tools
        return _names(
            self.allowed_tools(ctx, tools),
            f"agent {self.agent_type!r}: allowed_tools function result",
            TOOL_NAME_PATTERN,
        )

    def __repr__(self) -> str:
        return f"AgentSpec(agent_type={self.agent_type!r}, kind={self.kind.value!r})"
