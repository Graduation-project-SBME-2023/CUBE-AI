"""Static source scan for the vocabulary Phase 6.1 retired (plan.md Phase 6.1 step 3).

Cut-over 1 renamed the schema and the code to the Appendix P vocabulary in one
change. This scan keeps the old names from coming back: it fails on any
retired name in the production source of the Application API, the Agent
System (its tool packs included) and the frontend.

Where an old name may still appear, and why:

* **Migrations** (`application_api/migrations/versions/`) describe the schema
  as it was, so they are not scanned.
* **Old durable events**: `session_events` rows written before the cut-over
  keep their type, so the legacy `pipeline_agent_status` type is registered
  (event_registry.py) and accepted by the one frontend replay helper.
* **The problem catalog** lives in plan.md, which is not source.
* **Tool contracts**: no tool name, tool argument name or success result shape
  changes (plan.md, global rules). A line that keeps such a key carries a
  marker comment; the system prompts name the `output_summary` argument of
  `write_agent_output`.

Tests are not scanned: they assert the history of old migrations and the
absence of old names.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "Backend"
FRONTEND_ROOT = REPO_ROOT / "Frontend" / "OHIF-Viewer"

RETIRED = re.compile(
    r"agent_runs?\b|agent_run_id|\bAgentRun\b|agent-runs|AGENT_RUN_ID"
    r"|plan_steps|\bPlanStep|step_id\b|step_order|\bSTEP_ID\b"
    r"|ws_logs?\b|\bWSLog\b|\bWS_Log\b|\bWslog\b|msg_type"
    r"|input_refs|durable_notes|output_summary"
    r"|segmentation_runs|segmentation_run_id|\bSegmentationRun\b|segmentation-runs"
    r"|external_job_id|(?<!batch_)correlation_run_id|user_retained|system_render"
    r"|pipeline_agent_status|graph_version"
    r"|predictions_artifact_id|input_artifact_id|\blast_activity\b|extra_data"
    # Routes. `/segmentations/` only after a base URL or a quote: workspace paths
    # such as temp/segmentations/ are directories, not routes.
    r"|/chat_sessions|/chat_messages/|(?<![\w.])/segmentations/"
)

# The one marker a line uses to keep a tool-contract name (see the docstring).
TOOL_CONTRACT_MARKER = re.compile(
    r"tool (?:result key|argument name), kept|tool result shapes do not change"
)

# (file, retired name) pairs that are allowed, each with its reason.
ALLOWED: dict[str, set[str]] = {
    # The legacy type of old durable session_events rows (see the docstring).
    "Backend/agent_system/services/event_registry.py": {"pipeline_agent_status"},
    "Frontend/OHIF-Viewer/extensions/research-playground/src/utils/nodeStatusEvent.ts": {
        "pipeline_agent_status",
        "Pipeline_agent_status",
    },
    # The write_agent_output tool argument, named in the agent prompts.
    "Backend/agent_system/system_prompts/system_messages.py": {"output_summary"},
}

PYTHON_ROOTS = (
    BACKEND_ROOT / "application_api",
    BACKEND_ROOT / "agent_system",
    BACKEND_ROOT / "scripts",
)
PYTHON_FILES = (
    BACKEND_ROOT / "storage_paths.py",
    BACKEND_ROOT / "storage_write_guard.py",
)
FRONTEND_ROOTS = (
    FRONTEND_ROOT / "extensions",
    FRONTEND_ROOT / "platform" / "app" / "src",
    FRONTEND_ROOT / "modes",
)
FRONTEND_SUFFIXES = {".ts", ".tsx", ".js", ".jsx"}
SKIPPED_PARTS = {"node_modules", "dist", "__pycache__", "versions", ".webpack"}


def _sources() -> list[Path]:
    files = [path for path in PYTHON_FILES if path.is_file()]
    for root in PYTHON_ROOTS:
        files += [
            path for path in root.rglob("*.py")
            if not SKIPPED_PARTS.intersection(path.relative_to(root).parts)
        ]
    for root in FRONTEND_ROOTS:
        if not root.is_dir():
            continue
        files += [
            path for path in root.rglob("*")
            if path.suffix in FRONTEND_SUFFIXES
            and not SKIPPED_PARTS.intersection(path.relative_to(root).parts)
        ]
    return files


def _violations() -> list[str]:
    found = []
    for path in _sources():
        relative = path.relative_to(REPO_ROOT).as_posix()
        allowed = ALLOWED.get(relative, set())
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if TOOL_CONTRACT_MARKER.search(line):
                continue
            for match in RETIRED.finditer(line):
                if match.group(0) not in allowed:
                    found.append(f"{relative}:{number}: {match.group(0)}")
    return found


def test_the_scan_covers_every_source_tree():
    """A moved tree would silently scan nothing."""
    sources = _sources()
    for root in (*PYTHON_ROOTS, FRONTEND_ROOT / "extensions"):
        assert any(root in path.parents for path in sources), root
    assert (BACKEND_ROOT / "application_api" / "migrations" / "versions").is_dir()


def test_the_scan_still_finds_a_retired_name():
    assert RETIRED.search('run = run.get("agent_run_id")')
    assert RETIRED.search("ctx.step_id")
    assert not RETIRED.search('config["batch_correlation_id"]')
    assert not RETIRED.search("chat.last_activity_at")
    assert not RETIRED.search("agent_runtime(ctx)")


def test_no_retired_name_outside_the_allowed_places():
    violations = _violations()
    assert not violations, (
        "Phase 6.1 retired these names (plan.md, Appendix P section P.2). Use the new "
        "vocabulary:\n" + "\n".join(violations[:80])
    )
