import json
import logging
import os
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

from .segmentation.module import SegmentationModule


async def segmentation_tool(workspace_folder: str | None = None):
    """The main segmentation tool, containing all the needed logic
    :arg workspace_folder: The workspace folder to save outputs to.
    """
    try:
        if workspace_folder is None:
            raise ValueError("Please specify a workspace folder")

        folder_name = Path(workspace_folder).name
        session_id = folder_name.removeprefix("workspace_")
        agent = SegmentationModule(session_id=session_id)
        await agent.initialize()

        config_path = os.path.join(workspace_folder, "config.json")
        result = await agent.run(config_path, workspace_folder)

        outputs_dir = os.path.join(workspace_folder, "outputs")
        os.makedirs(outputs_dir, exist_ok=True)
        output_data = {
            "agent_id": "segmentation_agent",
            "agent_role": "roi_segmentation",
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "inputs": {"workspace_folder": workspace_folder},
            "outputs": {
                "files": {},
                "metrics": {},
                "summary": "Segmentation completed."
            },
            "errors": [],
            "warnings": []
        }
        output_path = Path(outputs_dir) / "segmentation_agent_output.json"
        output_path.write_text(json.dumps(output_data, indent=2), encoding="utf-8")

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
    async def create(cls, model_client=None, workspace_folder=None, session_id=None):
        model_client = OllamaClient() if not model_client else model_client
        mcp_server_path = os.path.abspath(
            os.path.join(current_dir, "..", "..", "..", "mcp", "server.py")
        )
        if not os.path.exists(mcp_server_path):
            raise FileNotFoundError(f"MCP server not found at: {mcp_server_path}")
        env = os.environ.copy()
        env.update({
            "WORKSPACE_DIR": workspace_folder,
            "SESSION_ID": session_id,
            "AGENT_ID": "segmentation_agent"
        })
        mcp_server = StdioServerParams(
            command=sys.executable,
            args=[mcp_server_path],
            env=env,
            read_timeout_seconds=2000,
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
            "write_agent_output"
        ]
        final_mcp_tools = cls.get_desired_tools(mcp_tools, desired_mcp_tools)
        logger.info(f"Creating segmentation agent")
        function_tools = [
            FunctionTool(
                segmentation_tool,
                description="The main segmentation tool",
            )
        ]
        tools = [*function_tools, *final_mcp_tools]
        logger.info(f"Tools initialized: {tools}")
        name = "segmentation_agent"
        description = "agent to run the segmentation task, has the capability to know which volumes to segment by itself"
        system_message = f"""{TASK_CONTEXT_BOOTSTRAP}

You are the segmentation agent. The workspace folder is: {workspace_folder}

Follow these steps in order:
1. Call read_agent_task_context to get your instructions.
2. Call scratchpad_register_agent(agent_id="segmentation_agent", role="roi_segmentation") to announce yourself.
3. Run the segmentation_tool with workspace_folder="{workspace_folder}".
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

=== STRUCTURED OUTPUT INSTRUCTIONS ===
After completing segmentation, you MUST call write_agent_output with:
  - agent_id: "segmentation_agent"
  - agent_role: "roi_segmentation"
  - status: "completed" (or "failed"/"partial")
  - inputs: {{"workspace_folder": "{workspace_folder}"}}
  - output_files: {{
      "labels_dir": "labels_data/",
      "volumes_dir": "volumes/",
      "masks_dir": "masks/"
    }}
  - output_metrics: {{
      "segmentation_method": "<method used, e.g. VoxTell text-prompt segmentation>",
      "volumes_segmented": <number>,
      "roi_description": "<what was segmented>"
    }}
  - output_summary: "<concise summary of segmentation results>"
  - warnings: [<any quality warnings, including any volumes that failed segmentation>]
"""
        return cls(
            name=name,
            model_client=model_client,
            tools=tools,
            description=description,
            system_message=system_message,
            max_tool_iterations=8,  # 6 steps required + buffer
        )

    @staticmethod
    def get_desired_tools(mcp_tools, desired_tools):
        return [tool for tool in mcp_tools if tool.name in desired_tools]

