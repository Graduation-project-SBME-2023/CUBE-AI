"""The toolkit stays framework-neutral (merged plan, Phase 1.5 "Done when").

No AutoGen, LangGraph, MCP or web-framework import, and nothing from the
agent system around it: the harness plugs in through protocols. Only
``toolkit/engines/`` (Plan 9) may import an agent framework.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TOOLKIT_ROOT = Path(__file__).resolve().parents[3] / "agent_system" / "toolkit"
ENGINES = "engines"

FORBIDDEN_ROOTS = frozenset({
    "autogen", "autogen_agentchat", "autogen_core", "autogen_ext",
    "langgraph", "langchain", "langchain_core",
    "claude_agent_sdk", "anthropic", "litellm",
    "mcp", "fastmcp",
    "fastapi", "starlette", "sqlalchemy",
    # the agent system around the toolkit (bare imports work in the container)
    "agents", "api", "app", "services", "socket_store", "teams", "tools",
})  # fmt: skip


def _modules() -> Iterator[Path]:
    for path in sorted(TOOLKIT_ROOT.rglob("*.py")):
        if ENGINES not in path.relative_to(TOOLKIT_ROOT).parts:
            yield path


def _absolute_imports(path: Path) -> Iterator[tuple[int, str]]:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
        if isinstance(node, ast.Import):
            yield from ((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module


def test_the_toolkit_has_modules_to_check():
    assert len(list(_modules())) > 10


def test_the_toolkit_imports_no_framework_and_no_harness_code():
    offending = [
        f"{path.relative_to(TOOLKIT_ROOT).as_posix()}:{line} imports {module}"
        for path in _modules()
        for line, module in _absolute_imports(path)
        if module.split(".")[0] in FORBIDDEN_ROOTS
        or (module.startswith("agent_system.") and not module.startswith("agent_system.toolkit"))
    ]
    assert offending == []
