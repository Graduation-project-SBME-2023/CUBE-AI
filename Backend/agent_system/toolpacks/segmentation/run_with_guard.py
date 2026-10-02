"""``run_segmentation_with_guard``: segment the session's approved cohort
(merged plan, Phase 2.4 step 3, L13).

Copied from ``segmentation_tool()`` in
``agents/execution_agents/segmentation_agent.py:61-134``, which stays frozen
with the rest of the AutoGen agents. There the one function had two names
(``segmentation_tool`` in the prompt, ``run_segmentation_with_guard`` as the
``FunctionTool``), which is why ``pipeline_monitor.CRITICAL_PIPELINE_TOOLS``
lists both. Here it has one name, and ``critical=True`` replaces that list: a
failure fails the node.

The AutoGen wrapper took ``workspace_folder`` and ``session_id`` from the
model, falling back to the environment. This tool takes no identity argument:
the session, the run and the workspace are the ``ToolContext``'s, so a model
cannot point it at another session.

Until Phase 9.7 moves ``SegmentationModule`` from its AutoGen/MCP workbench to
the agent engine, ``SegmentationModule.initialize()`` cannot start (it launched
``Backend/mcp/server.py``, gone since Phase 2.1).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from storage_paths import workspace_subpath

from .._toolkit import error_result, tool
from ..common import domain_api
from ..common.manifests import INTERIM_HEAVY
from ..common.workspace import workspace_dir

logger = logging.getLogger(__name__)


def _agent_output_status(status: str) -> str:
    """Map the module's own result to the three output states the contract
    allows (`completed` | `failed` | `partial`)."""
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "failed", "partial"}:
        return normalized
    if normalized in {"success", "ok", "succeeded"}:
        return "completed"
    return "failed"


def _agent_output(result: dict[str, Any], workspace: str) -> dict[str, Any]:
    """The structured result recorded on this node's run (not a workspace file)."""
    failures = [str(failure) for failure in result.get("failures", [])]
    return {
        "agent_role": "roi_segmentation",
        "status": _agent_output_status(result.get("status", "failed")),
        "inputs": {"workspace_folder": workspace},
        "output_files": {
            "segmentation_batch_id": result.get("segmentation_batch_id"),
            "volumes_dir": str(workspace_subpath(workspace, "volumes")),
            "masks_dir": str(workspace_subpath(workspace, "masks")),
        },
        "output_metrics": {
            "segmentation_method": result.get("segmentation_method"),
            "targets": result.get("targets", []),
            "pair_count": result.get("pair_count", 0),
            "accepted_count": result.get("accepted_count", 0),
            "reused_count": result.get("reused_count", 0),
            "generated_count": result.get("generated_count", 0),
        },
        "summary": (
            f"Segmentation {result.get('status', 'failed')} with "
            f"{result.get('accepted_count', 0)}/"
            f"{result.get('pair_count', 0)} accepted segmentation masks."
        ),
        "errors": failures if result.get("status") == "failed" else [],
        "warnings": failures if result.get("status") == "partial" else [],
    }


def _segmentation_module(session_id: str, run_id: str) -> Any:
    # Imported when called: the module pulls in SimpleITK and, until Phase 9.7,
    # AutoGen; neither belongs in the catalog's import of the pack.
    from agent_system.agents.execution_agents.segmentation.module import SegmentationModule

    return SegmentationModule(session_id=session_id, run_id=run_id)


@tool(
    name="run_segmentation_with_guard",
    visibility={"agent"},
    critical=True,
    **INTERIM_HEAVY,
)
async def run_segmentation_with_guard() -> dict[str, Any]:
    """The main segmentation tool: segments every volume of this session's
    approved cohort (reusing valid masks, generating the rest, with the review
    gate inside), records the batch and this node's output, and returns the
    segmentation summary."""
    run_id = domain_api.run_id()
    session_id = domain_api.session_id()
    if not run_id or not session_id:
        return error_result(
            "no_run",
            "Segmentation runs only inside a node: this call belongs to no run, so its "
            "result could not be recorded.",
        )
    workspace = workspace_dir()
    if not Path(workspace).is_dir():
        return error_result("not_found", f"The session workspace does not exist: {workspace}")

    module = _segmentation_module(session_id, run_id)
    try:
        await module.initialize()
        # The volumes, instructions and checkpoint come from PostgreSQL (the
        # approved cohort, this run's node, the batch).
        result = await module.run(str(workspace))
    finally:
        await module.close()

    # The structured result is a row on this node's run. There is deliberately
    # no file fallback: a write failure fails the call.
    try:
        await domain_api.request(
            "PUT", f"/runs/{run_id}/output", json=_agent_output(result, str(workspace))
        )
    except domain_api.DomainApiError as exc:
        return domain_api.error_result_from(exc, "The segmentation output was not recorded")

    if _agent_output_status(result.get("status", "failed")) == "failed":
        # The legacy monitor stopped the pipeline on a "failed" status; with
        # critical=True an error result is what fails the node now. A partial
        # result (some masks accepted) is still a success, as it was.
        return error_result(
            "segmentation_failed",
            f"Segmentation failed: {result.get('accepted_count', 0)}/"
            f"{result.get('pair_count', 0)} masks accepted.",
            segmentation=result,
        )
    return result
