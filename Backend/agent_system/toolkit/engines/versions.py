"""What a run freezes about the engine it started on (Phase 9.1).

A saved SDK session can only be resumed by the engine that wrote it, and only
for the agent definition it ran: the transcript format belongs to the SDK, and
a changed prompt or tool list makes the saved reasoning stale. So every run
records ``execution_policy.engine_version`` and ``execution_policy.spec_digest``,
and a resume that finds either changed fails ``transcript_incompatible``
(Phase 9.4) instead of continuing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

import claude_agent_sdk
from claude_agent_sdk._cli_version import __cli_version__


def engine_version() -> str:
    """The SDK and its bundled CLI, e.g. ``claude-agent-sdk 0.2.173 / cli 2.1.309``."""
    return f"claude-agent-sdk {claude_agent_sdk.__version__} / cli {__cli_version__}"


def spec_digest(
    *, prompt: str, tool_names: Iterable[str], delegates: Iterable[str], version: int
) -> str:
    """SHA-256 of what the agent ran with: its resolved prompt, its tools and
    subagents (order does not matter) and its spec ``version``."""
    content = {
        "prompt": prompt,
        "tools": sorted(set(tool_names)),
        "delegates": sorted(set(delegates)),
        "version": version,
    }
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
