"""ToolSpec: the manifest of one callable action (merged plan, Phase 1.1 step 1).

A tool is a callable action. An agent is a definition (``AgentSpec``) and is
never a tool, so nothing here knows about agents. The vocabulary the manifest
is written in (the enums, scope sets and queue map) lives in ``manifest.py``.

This module only describes a tool and checks that each field is well-formed on
its own. The cross-field manifest rules (``side_effects="none"`` <=> read-only,
``cost="heavy"`` => ``execution="job"``, the visibility rules, ...) are enforced
by ``catalog.validate()``, which sees every tool at once.

No AutoGen, LangGraph, or MCP imports: the toolkit is framework-neutral.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from .manifest import (
    MAX_PRIORITY,
    MIN_PRIORITY,
    PHI_SCOPES,
    RESOURCE_QUEUES,
    WRITE_SCOPES,
    Cost,
    DataScope,
    Execution,
    IsolationLevel,
    Resource,
    SideEffects,
    TrustLevel,
    Visibility,
    coerce_enum,
    coerce_enum_set,
)

if TYPE_CHECKING:
    # Filled by later steps of Phase 1.1 (ports.py, context.py). Annotation-only.
    from ..context import ToolContext
    from ..ports import PortSpec


# The name the model sees; the strictest common rule across chat-model tool APIs.
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

DEFAULT_TIMEOUT_S = 120.0

type ToolHandler = Callable[..., Awaitable[Any]]
# Turns a successful result into zero or more UI view events for the EventSink.
type OnResultHook = Callable[[Any, ToolContext], Iterable[Mapping[str, Any]]]


def _freeze_ports(ports: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    for port in ports:
        if not isinstance(port, str) or not port:
            raise ValueError(f"{field_name} port names must be non-empty strings: {port!r}")
    return MappingProxyType(dict(ports))


@dataclass(frozen=True, eq=False, kw_only=True)
class ToolSpec:
    """Everything the toolkit, the Executor and the Guardrails need to know
    about one tool. Instances are immutable and compared by identity: the
    registry holds exactly one spec per name.

    String values are accepted for every enum field (``execution="inline"``,
    ``visibility={"main", "agent"}``) and normalised in ``__post_init__``.
    ``copy.replace(spec, ...)`` builds a changed copy and re-runs every check.
    """

    # --- identity
    name: str
    description: str
    pack: str
    version: int = 1

    # --- callable and its arguments
    handler: ToolHandler
    input_model: type[BaseModel] | None = None  # first-party: built from the signature
    input_schema: Mapping[str, Any] | None = None  # external: raw JSON schema
    output_model: type[BaseModel] | None = None

    # --- ports (artifact types, see toolkit/artifacts/)
    inputs: Mapping[str, PortSpec] = field(default_factory=dict)
    outputs: Mapping[str, PortSpec] = field(default_factory=dict)

    # --- manifest
    side_effects: SideEffects
    cost: Cost
    resources: frozenset[Resource] = frozenset()
    execution: Execution
    trust: TrustLevel = TrustLevel.FIRST_PARTY
    isolation: IsolationLevel = IsolationLevel.INLINE
    data_scopes: frozenset[DataScope] = frozenset()
    visibility: frozenset[Visibility]

    # --- UI
    ui: str | None = None  # view type for the node, e.g. "modeling_results_view"
    on_result: OnResultHook | None = None

    # --- budgets and retry cap
    timeout_s: float = DEFAULT_TIMEOUT_S
    queue_timeout_s: float | None = None
    priority: int = MIN_PRIORITY
    max_attempts: int = 1

    # --- behaviour flags
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    critical: bool = False  # a failure fails the node (replaces CRITICAL_PIPELINE_TOOLS)

    def __post_init__(self) -> None:
        # Frozen dataclass: normalised values go through object.__setattr__.
        def _set(name: str, value: Any) -> None:
            object.__setattr__(self, name, value)

        if not isinstance(self.name, str) or not TOOL_NAME_PATTERN.match(self.name):
            raise ValueError(
                f"tool name {self.name!r} must match {TOOL_NAME_PATTERN.pattern}"
            )
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError(f"tool {self.name!r}: description must not be empty")
        if not isinstance(self.pack, str) or not self.pack.strip():
            raise ValueError(f"tool {self.name!r}: pack must not be empty")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError(f"tool {self.name!r}: version must be an int >= 1")
        if not callable(self.handler):
            raise TypeError(f"tool {self.name!r}: handler must be callable")

        if (self.input_model is None) == (self.input_schema is None):
            raise ValueError(
                f"tool {self.name!r}: set exactly one of input_model or input_schema"
            )
        for attr in ("input_model", "output_model"):
            model = getattr(self, attr)
            if model is not None and not (
                isinstance(model, type) and issubclass(model, BaseModel)
            ):
                raise TypeError(f"tool {self.name!r}: {attr} must be a pydantic model class")
        if self.input_schema is not None:
            _set("input_schema", MappingProxyType(dict(self.input_schema)))

        _set("inputs", _freeze_ports(self.inputs, "inputs"))
        _set("outputs", _freeze_ports(self.outputs, "outputs"))

        _set("side_effects", coerce_enum(SideEffects, self.side_effects, "side_effects"))
        _set("cost", coerce_enum(Cost, self.cost, "cost"))
        _set("execution", coerce_enum(Execution, self.execution, "execution"))
        _set("trust", coerce_enum(TrustLevel, self.trust, "trust"))
        _set("isolation", coerce_enum(IsolationLevel, self.isolation, "isolation"))
        _set("resources", coerce_enum_set(Resource, self.resources, "resources"))
        _set("data_scopes", coerce_enum_set(DataScope, self.data_scopes, "data_scopes"))
        _set("visibility", coerce_enum_set(Visibility, self.visibility, "visibility"))
        if not self.visibility:
            raise ValueError(f"tool {self.name!r}: visibility must name at least one audience")

        if self.ui is not None and (not isinstance(self.ui, str) or not self.ui.strip()):
            raise ValueError(f"tool {self.name!r}: ui must be a non-empty string or None")
        if self.on_result is not None and not callable(self.on_result):
            raise TypeError(f"tool {self.name!r}: on_result must be callable")

        if not self.timeout_s > 0:
            raise ValueError(f"tool {self.name!r}: timeout_s must be > 0")
        if self.queue_timeout_s is not None and not self.queue_timeout_s > 0:
            raise ValueError(f"tool {self.name!r}: queue_timeout_s must be > 0 or None")
        if not MIN_PRIORITY <= self.priority <= MAX_PRIORITY:
            raise ValueError(
                f"tool {self.name!r}: priority must be in [{MIN_PRIORITY}, {MAX_PRIORITY}]"
            )
        if self.max_attempts < 1:
            raise ValueError(f"tool {self.name!r}: max_attempts must be >= 1")

    # --- derived fields

    @property
    def phi(self) -> bool:
        """True when the tool reads or writes patient data."""
        return bool(self.data_scopes & PHI_SCOPES)

    @property
    def resource_class(self) -> str | None:
        """The Celery queue for a job run, derived from ``resources``.

        ``None`` when no resource maps to a compute queue (e.g. only ``llm``).
        """
        for resource, queue in RESOURCE_QUEUES:
            if resource in self.resources:
                return queue
        return None

    @property
    def write_scopes(self) -> frozenset[DataScope]:
        return self.data_scopes & WRITE_SCOPES

    def visible_to(self, audience: Visibility | str) -> bool:
        return coerce_enum(Visibility, audience, "audience") in self.visibility

    def args_schema(self) -> dict[str, Any]:
        """JSON schema of the arguments, whichever way the tool declared them."""
        if self.input_model is not None:
            return self.input_model.model_json_schema()
        return dict(self.input_schema or {})

    def __repr__(self) -> str:
        return f"ToolSpec(name={self.name!r}, pack={self.pack!r}, execution={self.execution.value!r})"
