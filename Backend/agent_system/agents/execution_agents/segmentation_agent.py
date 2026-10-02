import logging
import os
from clients.backend_client import BackendClient
from storage_paths import workspace_subpath
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
sys.path.append(root_dir)

TASK_CONTEXT_BOOTSTRAP = """=== TASK CONTEXT BOOTSTRAP (MANDATORY) ===
Before doing anything else, call read_agent_task_context exactly once.
Use the returned task_message.instructions and context_augmentation as the primary task specification.
"""

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.tools.mcp import mcp_server_tools, StdioServerParams
from autogen_core.tools import FunctionTool

from agent_system.clients.ollamaClient import OllamaClient
from services.timeout_policy import MCP_STANDARD_READ_SECONDS

from .segmentation.module import SegmentationModule, resolve_mcp_server_path


def _validated_runtime_context(
    workspace_folder: str | None,
    session_id: str | None,
) -> tuple[str, str]:
    if not workspace_folder:
        raise ValueError("workspace_folder is required")
    workspace = Path(workspace_folder).resolve()
    if not workspace.is_dir():
        raise ValueError(f"Workspace folder does not exist: {workspace}")

    clean_session_id = str(session_id or "").strip()
    if (
        not clean_session_id
        or clean_session_id in {".", ".."}
        or "/" in clean_session_id
        or "\\" in clean_session_id
    ):
        raise ValueError("A valid active session_id is required")
    return str(workspace), clean_session_id


def _agent_output_status(status: str) -> str:
    """Map the module's own result to the three output states the contract
    allows (`completed` | `failed` | `partial`)."""
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "failed", "partial"}:
        return normalized
    if normalized in {"success", "ok", "succeeded"}:
        return "completed"
    return "failed"


async def segmentation_tool(
    workspace_folder: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
):
    """The main segmentation tool, containing all the needed logic:arg workspace_folder: The workspace folder to save outputs to.:arg session_id: The active session ID.:arg run_id: The `node_runs` row this execution belongs to."""
    try:
        workspace_folder, session_id = _validated_runtime_context(
            workspace_folder,
            session_id,
        )
        agent = SegmentationModule(session_id=session_id, run_id=run_id)
        try:
            await agent.initialize()
            # current this flow: the volumes, instructions and checkpoint come from
            # PostgreSQL (the approved cohort, this run's step, the batch).
            result = await agent.run(workspace_folder)
        finally:
            await agent.close()

        # /: the structured result is a row on this step's run, not
        # `outputs/segmentation_agent_output.json`. There is deliberately no
        # file fallback — a PostgreSQL write failure fails the operation.
        output_data = {
            "agent_role": "roi_segmentation",
            "status": _agent_output_status(result.get("status", "failed")),
            "inputs": {"workspace_folder": workspace_folder},
            "output_files": {
                "segmentation_batch_id": result.get("segmentation_batch_id"),
                "volumes_dir": str(workspace_subpath(workspace_folder, "volumes")),
                "masks_dir": str(workspace_subpath(workspace_folder, "masks")),
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
            "errors": [
                str(failure)
                for failure in (
                    result.get("failures", [])
                    if result.get("status") == "failed"
                    else []
                )
            ],
            "warnings": [
                str(failure)
                for failure in (
                    result.get("failures", [])
                    if result.get("status") == "partial"
                    else []
                )
            ],
        }
        if not run_id:
            raise RuntimeError(
                "segmentation_tool has no run_id: its result cannot be "
                "recorded, and v1 does not fall back to a workspace file."
            )
        await BackendClient.write_agent_output(run_id, output_data)

        return result

    except Exception:
        logger.exception("Segmentation tool failed")
        raise


class SegmentationAgent(AssistantAgent):
    def __init__(
        self,
        name,
        model_client,
        tools,
        description,
        system_message,
        max_tool_iterations,
    ):
        super().__init__(
            name=name,
            model_client=model_client,
            tools=tools,
            description=description,
            system_message=system_message,
            max_tool_iterations=max_tool_iterations,
        )

    @classmethod
    async def create(cls, model_client=None, workspace_folder=None, session_id=None, node_name=None, run_id=None, node_id=None):
        model_client = OllamaClient() if not model_client else model_client
        workspace_folder, session_id = _validated_runtime_context(
            workspace_folder,
            session_id,
        )
        mcp_server_path = resolve_mcp_server_path()
        if not mcp_server_path.exists():
            raise FileNotFoundError(f"MCP server not found at: {mcp_server_path}")
        env = os.environ.copy()
        env.update({
            "WORKSPACE_DIR": workspace_folder,
            "SESSION_ID": session_id,
            "AGENT_ID": "segmentation_agent",
            "RUN_ID": str(run_id or ""),
            "NODE_ID": str(node_id or "")
        })
        mcp_server = StdioServerParams(
            command=sys.executable,
            args=[str(mcp_server_path)],
            env=env,
            read_timeout_seconds=MCP_STANDARD_READ_SECONDS,
        )
        try:
            mcp_tools = await mcp_server_tools(mcp_server)
        except Exception as exc:
            logger.exception("Failed to initialize MCP tools from %s", mcp_server_path)
            raise RuntimeError(
                f"Failed to initialize MCP tools from {mcp_server_path}"
            ) from exc
        desired_mcp_tools = [
            "read_agent_task_context",
            "scratchpad_register_agent",
            "scratchpad_write_summary",
            "scratchpad_set_status",
            "scratchpad_raise_flag",
        ]
        final_mcp_tools = cls.get_desired_tools(mcp_tools, desired_mcp_tools)
        logger.info(f"Creating segmentation agent")
        _execute_segmentation_tool = segmentation_tool

        async def run_segmentation_with_guard(
            workspace_folder: str | None = None,
            session_id: str | None = None,
        ):
            """The main segmentation tool, containing all the needed logic
            Arg workspace_folder: The workspace folder to save outputs to.
            Arg session_id: The active session ID.
            """
            clean_folder = workspace_folder if workspace_folder and str(workspace_folder).strip() not in {"...", ".", ".."} else None
            if clean_folder and not Path(clean_folder).exists():
                clean_folder = None
            effective_folder = clean_folder or env["WORKSPACE_DIR"]

            clean_id = session_id if session_id and str(session_id).strip() not in {"...", ".", ".."} else None
            effective_id = clean_id or env["SESSION_ID"]

            return await _execute_segmentation_tool(
                workspace_folder=effective_folder,
                session_id=effective_id,
                run_id=env["RUN_ID"] or None,
            )

        function_tools = [
            FunctionTool(
                run_segmentation_with_guard,
                description="The main segmentation tool",
            )
        ]
        tools = [*function_tools, *final_mcp_tools]
        logger.info(f"Tools initialized: {tools}")
        # The node name identifies THIS step's execution; it falls
        # back to the agent type for the standalone/dev entrypoints below.
        name = node_name or "segmentation_agent"
        description = "agent to run the segmentation task, has the capability to know which volumes to segment by itself"
        system_message = f"""{TASK_CONTEXT_BOOTSTRAP}

You are the segmentation agent. The workspace folder is: {workspace_folder}

Follow these steps in order:
1. Call read_agent_task_context to get your instructions.
2. Call scratchpad_register_agent(agent_id="segmentation_agent", role="roi_segmentation") to announce yourself.
3. Run segmentation_tool with workspace_folder="{workspace_folder}" and
   session_id="{session_id}".
4. After the tool completes, call scratchpad_write_summary to describe:
   - Segmentation method used
   - Number of volumes segmented
   - Any quality issues encountered
   - If any masks were modified by the user, explicitly note this and ensure downstream tasks use the user-modified mask.
5. Call scratchpad_set_status(agent_id="segmentation_agent", status="done").

Do NOT ask questions. Run the tool and report results.

=== SCRATCHPAD INSTRUCTIONS ===
At the very start of your task, register yourself:
  - scratchpad_register_agent(agent_id="segmentation_agent", role="roi_segmentation")

When segmentation is complete:
1. Call scratchpad_write_summary with:
   - Segmentation method used
   - Number of volumes segmented
   - Any quality issues encountered
   - Mention if any mask was user-modified
2. Call scratchpad_set_status(agent_id="segmentation_agent", status="done")

The segmentation tool writes the authoritative structured output itself.
Do not call another output writer or reconstruct its metrics.
"""
        return cls(
            name=name,
            model_client=model_client,
            tools=tools,
            description=description,
            system_message=system_message,
            max_tool_iterations=7,
        )

    @staticmethod
    def get_desired_tools(mcp_tools, desired_tools):
        return [tool for tool in mcp_tools if tool.name in desired_tools]

