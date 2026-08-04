import asyncio
import sys
import os
import logging

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
sys.path.append(root_dir)

from pathlib import Path
from autogen_agentchat.agents import AssistantAgent
from clients.ollamaClient import OllamaClient
from autogen_core.models import SystemMessage
from autogen_agentchat.ui import Console
from autogen_core import CancellationToken
from descriptions.models_descriptions import RESULT_AGENT_DESCRIPTION
from system_prompts.system_messages import RESULT_AGENT_SYSTEM_PROMPT
from autogen_ext.tools.mcp import mcp_server_tools, StdioServerParams
from services.timeout_policy import MCP_STANDARD_READ_SECONDS

logger = logging.getLogger(__name__)

TASK_CONTEXT_BOOTSTRAP = """=== TASK CONTEXT BOOTSTRAP (MANDATORY) ===
Before doing anything else, call read_agent_task_context exactly once.
Use the returned task_message.instructions and context_augmentation as the primary task specification.
If the tool returns status other than 'ok', raise a scratchpad flag and stop.
"""


# TODO: implement the mcp tool that takes the results of modeling as the results


class ResultReporterAgent(AssistantAgent):
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
            "AGENT_ID": "result_reporter_agent",
        })
        mcp_server = StdioServerParams(
            command=sys.executable,
            args=[mcp_server_path],
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
        desired_tools = [
            "read_agent_task_context",
            "get_modeling_results",
            "scratchpad_register_agent",
            "scratchpad_write_note",
            "scratchpad_raise_flag",
            "scratchpad_write_summary",
            "scratchpad_set_status",
            "scratchpad_read_agent",
            "read_agent_output",
            "read_all_agent_outputs",
            "write_agent_output",
        ]
        final_tools = cls.get_desired_tools(mcp_tools, desired_tools)
        # final_tools = mcp_tools
        name = "result_reporter_agent"
        description = RESULT_AGENT_DESCRIPTION
        system_message = f"{TASK_CONTEXT_BOOTSTRAP}\n\n{RESULT_AGENT_SYSTEM_PROMPT}"
        return cls(
            name=name,
            model_client=model_client,
            tools=final_tools,
            description=description,
            system_message=system_message,
            max_tool_iterations=16,
        )

    @staticmethod
    def get_desired_tools(mcp_tools, desired_tools):
        return [tool for tool in mcp_tools if tool.name in desired_tools]


async def main():
    # Initialize the agent using the factory method
    agent = await ResultReporterAgent.create()

    # Now you can use the agent normally
    print(f"Agent initialized with {agent._tools} tools.")
    print(agent._max_tool_iterations)
    for tool in agent._tools:
        print(tool.name)
    await Console(
        agent.run_stream(
            task="A radiomics-derived imaging signature from CT imaging can predict high-risk patients with neuroblastoma",
            cancellation_token=CancellationToken(),
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
