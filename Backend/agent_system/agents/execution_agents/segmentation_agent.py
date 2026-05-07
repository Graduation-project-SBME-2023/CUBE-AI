import asyncio
import sys
import os
import logging

logger = logging.getLogger(__name__)

TASK_CONTEXT_BOOTSTRAP = """=== TASK CONTEXT BOOTSTRAP (MANDATORY) ===
Before doing anything else, call read_agent_task_context exactly once.
Use the returned task_message.instructions and context_augmentation as the primary task specification.
If the tool returns status other than 'ok', raise a scratchpad flag and stop.
"""

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
sys.path.append(root_dir)

from pathlib import Path
from autogen_agentchat.agents import AssistantAgent
from agent_system.clients.ollamaClient import OllamaClient
from autogen_core.models import SystemMessage
from autogen_agentchat.ui import Console
from autogen_core import CancellationToken

# from agent_system.descriptions.models_descriptions import RESULT_AGENT_DESCRIPTION
# from agent_system.system_prompts.system_messages import RESULT_AGENT_SYSTEM_PROMPT
from autogen_ext.tools.mcp import mcp_server_tools, StdioServerParams

# from .segmentation_module import SegmentationModule
from autogen_core.tools import FunctionTool

from .segmentation.module import SegmentationModule


async def test_module(workspace_folder: str | None = None):
    """Test function for the SegmentationModule with return value
    :arg workspace_folder: The workspace folder to save outputs to.
    """
    try:
        # print("Starting SegmentationModule test...")

        # Initialize
        if workspace_folder is None:
            return RuntimeError("Please specify a workspace folder")

        folder_name = Path(workspace_folder).name
        session_id = folder_name.removeprefix("workspace_")
        agent = SegmentationModule(session_id=session_id)
        await agent.initialize()

        # current_file = Path(__file__)
        # config_path = os.path.join(current_file.parent.parent.parent, "workspace", "config.json")
        if workspace_folder is None:
            current_file = Path(__file__)
            workspace_folder = os.path.join(
                current_file.parent.parent.parent, "workspace"
            )
        config_path = os.path.join(workspace_folder, "config.json")
        # print(f"Running with config: {config_path}")

        # Run the agent and capture the result
        result = await agent.run(config_path, workspace_folder)

        # print("Test completed successfully!")
        # print(f"Result: {result}")

        return result

    except Exception as e:
        # print(f"Test failed with error: {e}", exc_info=True)
        raise e


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
            "write_agent_output",
        ]
        final_mcp_tools = cls.get_desired_tools(mcp_tools, desired_mcp_tools)
        logger.info(f"Creating segmentation agent")
        function_tools = [
            FunctionTool(
                test_module,
                description="the tool that starts the segmentation, it has the capability to know what to segment and install by itself",
            )
        ]
        tools = [*function_tools, *final_mcp_tools]
        logger.info(f"Tools initialized: {tools}")
        name = "segmentation_agent"
        description = "agent to run the segmentation task, has the capability to know which volumes to segment by itself"
        system_message = f"""{TASK_CONTEXT_BOOTSTRAP}

to start the task of the segmentation, all you need to do just run the tool you have, do not ask any questions, the workspace folder is {workspace_folder}

=== SCRATCHPAD INSTRUCTIONS ===
At the very start of your task, register yourself:
  - scratchpad_register_agent(agent_id="segmentation_agent", role="roi_segmentation")

When segmentation is complete:
1. Call scratchpad_write_summary with:
   - Segmentation method used
   - Number of volumes segmented
   - Any quality issues encountered
2. Call scratchpad_set_status(agent_id="segmentation_agent", status="done")

=== STRUCTURED OUTPUT INSTRUCTIONS ===
After completing segmentation, you MUST call write_agent_output with:
  - agent_id: "segmentation_agent"
  - agent_role: "roi_segmentation"
  - status: "completed" (or "failed"/"partial")
  - inputs: {{"workspace_folder": "{workspace_folder}"}}
  - output_files: {{"labels_dir": "labels_data/"}}
  - output_metrics: {{
      "segmentation_method": "<method used, e.g. MONAI Label VISTA3D>",
      "volumes_segmented": <number>,
      "roi_description": "<what was segmented>"
    }}
  - output_summary: "<concise summary of segmentation results>"
  - warnings: [<any quality warnings>]
"""
        return cls(
            name=name,
            model_client=model_client,
            tools=tools,
            description=description,
            system_message=system_message,
            max_tool_iterations=2,
        )

    @staticmethod
    def get_desired_tools(mcp_tools, desired_tools):
        return [tool for tool in mcp_tools if tool.name in desired_tools]


async def main():
    # Initialize the agent using the factory method
    agent = await SegmentationAgent.create()

    # # Now you can use the agent normally
    # print(f"Agent initialized with {agent._tools} tools.")
    # print(agent._max_tool_iterations)
    # for tool in agent._tools:
    #     print(tool.name)
    await Console(
        agent.run_stream(
            task="run segmentation", cancellation_token=CancellationToken()
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
