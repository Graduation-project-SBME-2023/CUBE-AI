"""The ``@tool`` decorator and the tool registry (merged plan, Phase 1.3 step 1).

``@tool(...)`` turns an async function into a ``ToolSpec`` and records it on
the function; ``catalog.build()`` collects the specs from the discovered
tool-pack modules with ``tools_in()``, so there is no central import list. The
decorated function is returned unchanged and stays directly callable in tests.

The argument model is built from the signature the way FastMCP built it
(``{function}Arguments``, one field per parameter), so the schemas the models
see do not change when a tool moves off ``@mcp.tool``. A ``ToolContext``
parameter is not an argument: dispatch fills it (``ToolSpec.context_param``).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from types import ModuleType
from typing import Any

from pydantic import BaseModel, create_model

from ..agents.spec import AgentKind, AgentSpec
from ..context import ToolContext
from ..manifest import Visibility
from ..registry import Registry, UnknownNameError
from .spec import ToolHandler, ToolSpec

TOOL_SPEC_ATTR = "__tool_spec__"
TOOLPACKS_PACKAGE = "toolpacks"


class UnknownToolError(UnknownNameError):
    kind = "tool"


class ToolNotAllowedError(PermissionError):
    """An agent's allowlist names a tool its kind of agent may not use."""


def tool[F: ToolHandler](
    *,
    name: str | None = None,
    description: str | None = None,
    pack: str | None = None,
    **manifest: Any,
) -> Callable[[F], F]:
    """Declare an async function as a tool.

    ``name`` defaults to the function name, ``description`` to its docstring
    and ``pack`` to the tool pack the module lives in
    (``agent_system.toolpacks.<pack>...``). ``manifest`` takes the remaining
    ``ToolSpec`` fields: ``side_effects``, ``cost``, ``execution``,
    ``visibility``, ``resources``, ``data_scopes``, ports, budgets, flags.
    """

    def decorate(fn: F) -> F:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"tool {fn.__qualname__} must be an async function")
        context_param, input_model = _arguments(fn)
        spec = ToolSpec(
            name=name or fn.__name__,
            description=description or inspect.getdoc(fn) or "",
            pack=pack or _pack_of(fn),
            handler=fn,
            input_model=input_model,
            context_param=context_param,
            **manifest,
        )
        setattr(fn, TOOL_SPEC_ATTR, spec)
        return fn

    return decorate


def spec_of(obj: object) -> ToolSpec | None:
    """The spec ``@tool`` recorded on ``obj``, if any."""
    spec = getattr(obj, TOOL_SPEC_ATTR, None)
    return spec if isinstance(spec, ToolSpec) else None


def tools_in(module: ModuleType) -> list[ToolSpec]:
    """Every tool defined or imported at the top level of ``module``, once each."""
    specs = (spec_of(value) for value in vars(module).values())
    return list({id(spec): spec for spec in specs if spec is not None}.values())


def _arguments(fn: Callable[..., Any]) -> tuple[str | None, type[BaseModel]]:
    """Split the signature into the ToolContext parameter and the argument model."""
    try:
        signature = inspect.signature(fn, eval_str=True)
    except NameError as exc:
        raise TypeError(f"cannot resolve the annotations of tool {fn.__qualname__}: {exc}") from exc

    context_param: str | None = None
    fields: dict[str, Any] = {}
    for param in signature.parameters.values():
        where = f"tool {fn.__qualname__}, parameter {param.name!r}"
        if isinstance(param.annotation, type) and issubclass(param.annotation, ToolContext):
            if context_param is not None:
                raise TypeError(f"{where}: a tool takes at most one ToolContext")
            context_param = param.name
            continue
        if param.kind not in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY):
            raise TypeError(f"{where}: arguments are passed by name; no *args, **kwargs or /")
        if param.annotation is param.empty:
            raise TypeError(f"{where}: needs a type annotation (it becomes the model's schema)")
        if param.name.startswith("_") or hasattr(BaseModel, param.name):
            raise TypeError(f"{where}: the name is reserved by the argument model")
        default = ... if param.default is param.empty else param.default
        fields[param.name] = (param.annotation, default)

    return context_param, create_model(f"{fn.__name__}Arguments", **fields)


def _pack_of(fn: Callable[..., Any]) -> str:
    parts = fn.__module__.split(".")
    if TOOLPACKS_PACKAGE in parts[:-1]:
        return parts[parts.index(TOOLPACKS_PACKAGE) + 1]
    raise TypeError(
        f"tool {fn.__qualname__} is not inside a {TOOLPACKS_PACKAGE} package; pass pack="
    )


class ToolRegistry(Registry[ToolSpec]):
    item_type = ToolSpec
    unknown_error = UnknownToolError

    def visible_to(self, audience: Visibility | str) -> tuple[ToolSpec, ...]:
        return tuple(spec for spec in self if spec.visible_to(audience))

    def for_main(self) -> tuple[ToolSpec, ...]:
        """The tools the Main Agent may call directly."""
        return self.visible_to(Visibility.MAIN)

    def graph_tools(self) -> tuple[ToolSpec, ...]:
        """The tools that can be submitted as Tool Nodes."""
        return self.visible_to(Visibility.GRAPH)

    def by_pack(self) -> dict[str, tuple[ToolSpec, ...]]:
        packs: dict[str, list[ToolSpec]] = {}
        for spec in self:
            packs.setdefault(spec.pack, []).append(spec)
        return {pack: tuple(specs) for pack, specs in sorted(packs.items())}

    def for_agent(self, agent: AgentSpec, ctx: ToolContext) -> tuple[ToolSpec, ...]:
        """The tools ``agent`` gets for this run, in its allowlist's order.

        Control agents choose among the ``main`` tools, every other agent among
        the ``agent`` tools; a function-valued allowlist only ever sees those,
        and a fixed one naming anything else is refused.
        """
        audience = Visibility.MAIN if agent.kind is AgentKind.CONTROL else Visibility.AGENT
        candidates = {spec.name: spec for spec in self.visible_to(audience)}
        names = agent.tools_for(ctx, tuple(candidates.values()))
        refused = [name for name in names if name not in candidates]
        for name in refused:
            self.get(name)  # an unknown name raises UnknownToolError first
        if refused:
            raise ToolNotAllowedError(
                f"agent {agent.agent_type!r} may not use {refused}: "
                f"not visible to {audience.value!r}"
            )
        return tuple(candidates[name] for name in names)
