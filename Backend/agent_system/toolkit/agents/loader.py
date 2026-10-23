"""The ``agent.md`` loader (merged plan, Phase 3.1).

Every agent is a directory ``agentpacks/<pack>/<agent_type>/`` holding:

- ``agent.md``: a YAML front matter with the declarative fields, and the
  system prompt as its body;
- optionally ``hooks.py``: the parts that are code, found by name:

  ===================== ==========================================================
  ``completion_check``  async ``(ctx, output) -> issues``; empty: the agent may finish
  ``context_builder``   ``(task, inputs, ctx) -> str``: the "Node inputs" message
  ``allowed_tools_fn``  ``(ctx, tools) -> names``; replaces a fixed ``allowed_tools``
  ``output_contract``   the pydantic model the agent's final output must match
  ``on_complete``       async ``(ctx, output) -> output``: records what the
                        output names, once the check passes (Phase 10.1)
  ``extract_<port>``    the ``extract`` of the output port ``<port>``
  ===================== ==========================================================

  Every other public name is free, except that an ``extract_`` name must
  match an output port (import a helper under a private alias if needed).

  A directory with ``hooks.py`` is a package (it has an ``__init__.py``), so
  the catalog imports the hooks under the same root as the toolkit; the
  loader is given that package name and never imports by file path.

Output ports are written ``Type``, ``Type[]`` (many) and ``Type?`` (optional),
the same labels ``catalog.index()`` shows. Every problem is raised as an
``AgentFileError`` that names the file.

The front matter carries the Claude Agent SDK fields too (``effort``,
``skills``, ``max_budget_usd``, ``gate_timeout_s``; see ``AgentSpec``). The
fields of the design before the SDK (``gates``, ``workflow``,
``human_in_the_loop``, ``max_context_tokens``) are refused with a message
that names what replaced them.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..frontmatter import FrontMatterError, read_front_matter
from ..ports import PortSpec
from .spec import (
    DEFAULT_MAX_TOOL_ITERATIONS,
    DEFAULT_MODEL_PROFILE,
    DEFAULT_TIMEOUT_S,
    AgentSpec,
)

HOOKS_MODULE = "hooks"
HOOK_NAMES = (
    "completion_check",
    "context_builder",
    "allowed_tools_fn",
    "output_contract",
    "on_complete",
)
EXTRACT_PREFIX = "extract_"

# Fields an author may reach for that do not belong in the front matter.
_MISPLACED = {
    "system_prompt": "the body of agent.md is the system prompt",
    **{name: "define it in hooks.py" for name in HOOK_NAMES if name != "allowed_tools_fn"},
}
# Fields of the design before the Claude Agent SDK, and what replaced them.
_RETIRED = {
    "gates": (
        "there is no fixed gate inside a node (D28); the agent asks with ask_user "
        "or AskUserQuestion only when the node instructions say the user wants it"
    ),
    "human_in_the_loop": "questions are AskUserQuestion and the ask_user forms (Phase 9.3)",
    "workflow": (
        "list the internal agents under delegates: they are SDK subagents, "
        "used in any order (Phase 9.6)"
    ),
    "max_context_tokens": "the SDK compacts the session by itself",
}

_PORT_LABEL = re.compile(r"^(?P<type>[A-Za-z_][A-Za-z0-9_]*)(?P<many>\[\])?(?P<optional>\?)?$")


class AgentFileError(ValueError):
    """An ``agent.md`` file, or its hooks, cannot become an ``AgentSpec``."""

    def __init__(self, path: Path, problem: str) -> None:
        self.path = path
        self.problem = problem
        super().__init__(f"{path}: {problem}")


class AgentFrontMatter(BaseModel):
    """The front matter fields. Strict: YAML already gives real ints, floats,
    bools and lists, so a quoted ``"12"`` is a mistake, not a number; and an
    unknown key is a typo, not an extension. Fields left out keep the
    ``AgentSpec`` defaults."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    agent_type: str
    kind: str
    description: str
    version: int = 1
    model_profile: str = DEFAULT_MODEL_PROFILE
    allowed_tools: list[str] = Field(default_factory=list)
    delegates: list[str] = Field(default_factory=list)
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_budget_usd: float | None = None
    resources: list[str] = Field(default_factory=lambda: ["llm"])
    max_completion_retries: int = 0
    ui: str | None = None
    parent: str | None = None
    resumable: bool | None = None
    gate_timeout_s: float | None = None
    effort: str | None = None
    skills: list[str] = Field(default_factory=list)


def load_agent_file(path: Path, package: str | None = None) -> AgentSpec:
    """Build the ``AgentSpec`` of one ``agent.md``.

    ``package`` is the dotted name of the file's directory as a package; it is
    needed only when the directory has a ``hooks.py``.
    """
    path = Path(path)
    try:
        data, body = read_front_matter(path)
    except (OSError, UnicodeDecodeError, FrontMatterError) as exc:
        raise AgentFileError(path, str(exc)) from None
    front = _front_matter(path, data)
    if front.agent_type != path.parent.name:
        raise AgentFileError(
            path,
            f"agent_type {front.agent_type!r} must match its directory name "
            f"{path.parent.name!r}",
        )

    hooks = _hooks(path, package)
    fields = front.model_dump(exclude_unset=True)
    fields["system_prompt"] = body
    for name in ("inputs", "outputs"):
        if name in fields:
            fields[name] = _ports(path, name, fields[name], hooks)
    _check_extracts(path, front.outputs, hooks)

    for name in HOOK_NAMES:
        hook = getattr(hooks, name, None)
        if hook is None:
            continue
        if name == "allowed_tools_fn":
            if "allowed_tools" in fields:
                raise AgentFileError(
                    path, "set allowed_tools in the front matter or allowed_tools_fn in "
                    "hooks.py, not both"
                )
            fields["allowed_tools"] = hook
        else:
            fields[name] = hook

    try:
        return AgentSpec(**fields)
    except (TypeError, ValueError) as exc:
        raise AgentFileError(path, str(exc)) from None


def _front_matter(path: Path, data: dict[str, Any]) -> AgentFrontMatter:
    for name, reason in _MISPLACED.items():
        if name in data:
            raise AgentFileError(path, f"{name!r} is not a front matter field: {reason}")
    for name, replacement in _RETIRED.items():
        if name in data:
            raise AgentFileError(path, f"{name!r} is no longer a front matter field: {replacement}")
    try:
        return AgentFrontMatter.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'front matter'}: {error['msg']}"
            for error in exc.errors()
        )
        raise AgentFileError(path, problems) from None


def _hooks(path: Path, package: str | None) -> ModuleType | None:
    if not (path.parent / f"{HOOKS_MODULE}.py").is_file():
        return None
    if not (path.parent / "__init__.py").is_file():
        raise AgentFileError(
            path, f"{HOOKS_MODULE}.py needs an __init__.py next to it, so the pack imports it"
        )
    if package is None:
        raise AgentFileError(path, f"{HOOKS_MODULE}.py found but no package name was given")
    name = f"{package}.{HOOKS_MODULE}"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name is not None and f"{name}.".startswith(f"{exc.name}."):
            # A directory on the way is not a package: the hooks themselves are fine.
            raise AgentFileError(path, f"cannot import {name}: {exc}") from None
        raise  # the hooks import something that is missing: a broken pack, not skipped


def _ports(
    path: Path, direction: str, labels: dict[str, str], hooks: ModuleType | None
) -> dict[str, PortSpec]:
    ports = {}
    for port, label in labels.items():
        match = _PORT_LABEL.match(label)
        if not match:
            raise AgentFileError(
                path,
                f"{direction} port {port!r}: {label!r} is not Type, Type[] or Type?",
            )
        extract = None
        if direction == "outputs":
            extract = getattr(hooks, f"{EXTRACT_PREFIX}{port}", None)
        try:
            ports[port] = PortSpec(
                match["type"],
                many=match["many"] is not None,
                required=match["optional"] is None,
                extract=extract,
            )
        except (TypeError, ValueError) as exc:
            raise AgentFileError(path, f"{direction} port {port!r}: {exc}") from None
    return ports


def _check_extracts(path: Path, outputs: dict[str, str], hooks: ModuleType | None) -> None:
    """An ``extract_<name>`` hook without an output port ``<name>`` is a typo."""
    for name in sorted(vars(hooks) if hooks else ()):
        if name.startswith(EXTRACT_PREFIX) and name.removeprefix(EXTRACT_PREFIX) not in outputs:
            raise AgentFileError(
                path, f"hooks.py defines {name} but there is no output port "
                f"{name.removeprefix(EXTRACT_PREFIX)!r}"
            )
