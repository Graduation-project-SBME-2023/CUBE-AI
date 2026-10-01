"""The catalog: every tool, agent and artifact type, discovered and validated
(merged plan, Phase 1.3 step 3).

``build()`` imports the pack packages (``toolpacks``, ``agentpacks`` and the
toolkit's own ``delegation``) and every module under them, plus any module
published under the ``cubeai.toolpacks`` / ``cubeai.agentpacks`` entry-point
groups, and collects what those modules define at top level: ``@tool``
functions, ``AgentSpec`` and ``ArtifactType`` objects. ``agent.md`` files under
the packs go through the agent-file loader (Plan 3). There is no central
import list: a new pack is found by being there.

``validate()`` runs every cross-spec rule at start-up and raises once with all
the problems. ``resolve()``, ``index()`` and ``describe()`` are what the
harness, the Main Agent and the Composer read.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.metadata import entry_points
from pathlib import Path
from types import ModuleType
from typing import Any

from .agents.registry import AgentRegistry
from .agents.spec import AgentKind, AgentSpec
from .artifacts.base import ArtifactType, ArtifactTypeRegistry
from .artifacts.builtin import builtin_registry
from .manifest import Visibility
from .ports import PortSpec
from .registry import Registry, UnknownNameError, reachable
from .tools.consistency import check_manifest
from .tools.registry import ToolRegistry, spec_of
from .tools.spec import ToolSpec

logger = logging.getLogger(__name__)

# "agent_system", or "" when agent_system itself is on sys.path (the container).
# Packs are imported under the same root as the toolkit, so their specs are
# instances of these very classes.
_ROOT = __package__.rpartition(".")[0]
PACK_PACKAGES: tuple[str, ...] = tuple(
    f"{_ROOT}.{name}" if _ROOT else name
    for name in ("toolkit.delegation", "toolpacks", "agentpacks")
)
ENTRY_POINT_GROUPS: tuple[str, ...] = ("cubeai.toolpacks", "cubeai.agentpacks")
AGENT_FILE = "agent.md"

MAIN_AGENT = "main_agent"
CONTROL_FORBIDDEN_TOOLS = frozenset({"run_agent", "plan", "recover"})
MAIN_AGENT_EXCEPTIONS = frozenset({"plan", "recover"})

type Spec = ToolSpec | AgentSpec
type AgentFileLoader = Callable[[Path], AgentSpec]
# Plan 4's sandbox policy: returns what is wrong with a tool, empty when nothing is.
type PolicyHook = Callable[[ToolSpec], Iterable[str]]


class CatalogError(ValueError):
    """The catalog cannot be built or is not valid; ``problems`` lists every reason."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(
            f"{len(problems)} catalog problem(s):\n" + "\n".join(f"- {p}" for p in problems)
        )


class UnknownRefError(UnknownNameError):
    kind = "tool or agent"


class IndexAudience(StrEnum):
    MAIN = "main"  # what Main may call directly or submit as a node
    COMPOSER = "composer"  # what the Composer may put in a graph


@dataclass(frozen=True)
class Catalog:
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    agents: AgentRegistry = field(default_factory=AgentRegistry)
    artifact_types: ArtifactTypeRegistry = field(default_factory=builtin_registry)
    policy_hooks: tuple[PolicyHook, ...] = ()

    # --- lookup

    def resolve(self, name: str) -> Spec:
        """The tool or agent called ``name``; replaces ``node_resolver``."""
        for registry in (self.tools, self.agents):
            if name in registry:
                return registry.get(name)
        raise UnknownRefError(name)

    # --- validation

    def validate(self) -> None:
        if problems := self.problems():
            raise CatalogError(problems)

    def problems(self) -> list[str]:
        both = sorted(self.tools.names() & self.agents.names())
        return [
            *(f"{name!r} is both a tool and an agent" for name in both),
            *(problem for spec in self.tools for problem in self._tool_problems(spec)),
            *(problem for agent in self.agents for problem in self._agent_problems(agent)),
            *self.artifact_types.problems(),
        ]

    def _tool_problems(self, spec: ToolSpec) -> Iterator[str]:
        what = f"tool {spec.name!r}"
        for violation in check_manifest(spec):
            yield f"{what}: {violation.message} [{violation.rule}]"
        yield from self._port_problems(what, spec)
        for hook in self.policy_hooks:
            yield from (f"{what}: {problem}" for problem in hook(spec))

    def _agent_problems(self, agent: AgentSpec) -> Iterator[str]:
        what = f"agent {agent.name!r}"
        yield from (f"{what}: {problem}" for problem in self._agent_rules(agent))
        yield from self._port_problems(what, agent)

    def _agent_rules(self, agent: AgentSpec) -> Iterator[str]:
        if not agent.has_dynamic_tools:
            unknown, hidden = self.tools.screen(agent, agent.allowed_tools)
            audience = self.tools.audience_of(agent)
            yield from (f"allowlist names unknown tool {name!r}" for name in unknown)
            yield from (f"may not use {name!r}: not visible to {audience}" for name in hidden)
            if agent.kind is AgentKind.CONTROL:
                allowed = MAIN_AGENT_EXCEPTIONS if agent.name == MAIN_AGENT else frozenset()
                for name in sorted(set(agent.allowed_tools) & (CONTROL_FORBIDDEN_TOOLS - allowed)):
                    yield f"a control agent may not use {name!r}"

        if not agent.is_graph_node and (agent.inputs or agent.outputs):
            yield f"only execution agents have ports, not {agent.kind} agents"

        internal = agent.kind is AgentKind.INTERNAL
        if internal and agent.parent is None:
            yield "an internal agent needs a parent"
        if not internal and agent.parent is not None:
            yield f"only internal agents have a parent, not {agent.kind} agents"
        if agent.parent is not None:
            if agent.parent not in self.agents:
                yield f"unknown parent {agent.parent!r}"
            elif internal and self.agents.get(agent.parent).kind is AgentKind.CONTROL:
                yield (
                    f"parent {agent.parent!r} is a control agent; "
                    "only execution and internal agents start internal agents"
                )

        # Control agents never get run_agent, so their delegates could never run.
        if agent.kind is AgentKind.CONTROL and agent.delegates:
            yield f"a control agent has no delegates, not {list(agent.delegates)}"
        for name in agent.delegates:
            if name not in self.agents:
                yield f"unknown delegate {name!r}"
                continue
            delegate = self.agents.get(name)
            if delegate.kind is not AgentKind.INTERNAL:
                yield f"delegate {name!r} is not an internal agent"
            elif delegate.parent != agent.name:
                yield f"delegate {name!r} has parent {delegate.parent!r}, not {agent.name!r}"
        if agent.name in reachable(agent.name, self._delegates):
            yield "is in a delegation cycle"

    def _delegates(self, name: str) -> tuple[str, ...]:
        return self.agents.get(name).delegates if name in self.agents else ()

    def _port_problems(self, what: str, spec: Spec) -> Iterator[str]:
        for direction, ports in (("input", spec.inputs), ("output", spec.outputs)):
            for port_name, port in ports.items():
                if port.artifact_type not in self.artifact_types:
                    yield (
                        f"{what}: {direction} port {port_name!r} names unknown "
                        f"artifact type {port.artifact_type!r}"
                    )

    # --- what the models read

    def index(self, audience: IndexAudience | str = IndexAudience.MAIN) -> list[dict[str, Any]]:
        """Compact entries (system §33): name, kind, one-line description, port
        types, cost, and how the entry is used ("call" directly or as a "node")."""
        audience = IndexAudience(audience)
        entries = []
        for spec in (*self.tools, *self.agents):
            uses = _uses(spec, audience)
            if uses:
                entries.append(
                    _compact(
                        name=spec.name,
                        kind=_kind(spec),
                        description=spec.description.strip().splitlines()[0],
                        inputs={name: _port_label(port) for name, port in spec.inputs.items()},
                        outputs={name: _port_label(port) for name, port in spec.outputs.items()},
                        cost=_json(spec.cost) if isinstance(spec, ToolSpec) else None,
                        use=uses,
                    )
                )
        return entries

    def describe(self, name: str) -> dict[str, Any]:
        """Everything about one tool or agent: schema, ports and manifest."""
        spec = self.resolve(name)
        described = {
            "name": spec.name,
            "kind": _kind(spec),
            "description": spec.description,
            "version": spec.version,
            "inputs": _ports(spec.inputs),
            "outputs": _ports(spec.outputs),
            "ui": spec.ui,
        }
        if isinstance(spec, ToolSpec):
            return described | {
                "pack": spec.pack,
                "args_schema": spec.args_schema(),
                "manifest": _fields(spec, TOOL_MANIFEST) | {"phi": spec.phi},
            }
        return described | _fields(spec, AGENT_MANIFEST) | {
            "agent_kind": spec.kind.value,
            "tools": "dynamic" if spec.has_dynamic_tools else list(spec.allowed_tools),
            "output_schema": (
                spec.output_contract.model_json_schema() if spec.output_contract else None
            ),
        }


TOOL_MANIFEST = (
    "side_effects", "cost", "resources", "resource_class", "execution", "trust",
    "isolation", "data_scopes", "visibility", "timeout_s", "queue_timeout_s",
    "priority", "max_attempts", "read_only", "destructive", "idempotent", "critical",
)  # fmt: skip
AGENT_MANIFEST = (
    "model_profile", "delegates", "max_tool_iterations", "timeout_s",
    "resources", "max_completion_retries", "human_in_the_loop", "parent",
)  # fmt: skip


def _kind(spec: Spec) -> str:
    return "tool" if isinstance(spec, ToolSpec) else "agent"


def _uses(spec: Spec, audience: IndexAudience) -> list[str]:
    if isinstance(spec, AgentSpec):
        return ["node"] if spec.is_graph_node else []
    uses = []
    if audience is IndexAudience.MAIN and spec.visible_to(Visibility.MAIN):
        uses.append("call")
    if spec.visible_to(Visibility.GRAPH):
        uses.append("node")
    return uses


def _port_label(port: PortSpec) -> str:
    """``MaskSet``; ``MaskSet[]`` for many; ``FeatureImportance?`` when optional."""
    return f"{port.artifact_type}{'[]' if port.many else ''}{'' if port.required else '?'}"


def _ports(ports: Mapping[str, PortSpec]) -> dict[str, dict[str, Any]]:
    return {
        name: _compact(
            type=port.artifact_type, many=port.many, required=port.required, param=port.param
        )
        for name, port in ports.items()
    }


def _fields(spec: Spec, names: Iterable[str]) -> dict[str, Any]:
    return {name: _json(getattr(spec, name)) for name in names}


def _json(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, (set, frozenset)):
        return sorted(_json(item) for item in value)
    if isinstance(value, tuple):
        return [_json(item) for item in value]
    return value


def _compact(**entry: Any) -> dict[str, Any]:
    return {key: value for key, value in entry.items() if value not in (None, {}, [])}


# --- discovery


def build(
    *,
    packages: Iterable[str] = PACK_PACKAGES,
    entry_point_groups: Iterable[str] = ENTRY_POINT_GROUPS,
    load_agent_file: AgentFileLoader | None = None,
    policy_hooks: Iterable[PolicyHook] = (),
) -> Catalog:
    """Discover every pack and return the catalog. Call ``validate()`` next.

    Raises ``CatalogError`` when two different specs claim one name, or when
    ``agent.md`` files exist but no loader was given.
    """
    roots = _pack_roots(packages, entry_point_groups)
    tools: list[ToolSpec] = []
    agents: list[AgentSpec] = []
    artifact_types: list[ArtifactType] = []
    for module in (module for root in roots for module in _modules(root)):
        for value in vars(module).values():
            if spec := spec_of(value):
                tools.append(spec)
            elif isinstance(value, AgentSpec):
                agents.append(value)
            elif isinstance(value, ArtifactType):
                artifact_types.append(value)

    problems: list[str] = []
    agent_files = sorted({path for root in roots for path in _agent_files(root)})
    if agent_files:
        if load_agent_file is None:
            problems.append(f"found {len(agent_files)} {AGENT_FILE} file(s) but no agent loader")
        else:
            agents.extend(load_agent_file(path) for path in agent_files)

    catalog = Catalog(policy_hooks=tuple(policy_hooks))
    for registry, items in (
        (catalog.tools, tools),
        (catalog.agents, agents),
        (catalog.artifact_types, artifact_types),
    ):
        problems.extend(_register_all(registry, items))
    if problems:
        raise CatalogError(problems)
    return catalog


def _pack_roots(packages: Iterable[str], groups: Iterable[str]) -> list[ModuleType]:
    roots: list[ModuleType] = []
    for name in packages:
        try:
            roots.append(importlib.import_module(name))
        except ModuleNotFoundError as exc:
            if exc.name != name:  # the pack exists but one of its imports is missing
                raise
            logger.debug("pack package %s does not exist; skipped", name)
    for group in groups:
        for entry_point in entry_points(group=group):
            module = entry_point.load()
            if not isinstance(module, ModuleType):
                raise CatalogError([f"entry point {entry_point.name!r} ({group}) is not a module"])
            roots.append(module)
    return roots


def _modules(root: ModuleType) -> Iterator[ModuleType]:
    """``root`` and, for a package, every module below it. Import errors propagate."""
    yield root
    if hasattr(root, "__path__"):
        prefix = f"{root.__name__}."
        for info in pkgutil.walk_packages(root.__path__, prefix, onerror=_reraise):
            yield importlib.import_module(info.name)


def _reraise(_name: str) -> None:
    raise  # pkgutil calls this while handling the ImportError; surface it


def _agent_files(root: ModuleType) -> Iterator[Path]:
    for directory in getattr(root, "__path__", ()):
        yield from Path(directory).rglob(AGENT_FILE)


def _register_all(registry: Registry[Any], items: Iterable[Any]) -> list[str]:
    problems = []
    for item in items:
        try:
            registry.register(item)
        except ValueError as exc:  # a second spec under a taken name
            problems.append(str(exc))
    return problems
