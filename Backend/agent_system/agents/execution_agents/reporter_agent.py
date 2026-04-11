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
from descriptions.models_descriptions import REPORTER_AGENT_DESCRIPTION
from system_prompts.system_messages import REPORTER_AGENT_SYSTEM_MESSAGE
from autogen_ext.tools.mcp import mcp_server_tools, StdioServerParams

logger = logging.getLogger(__name__)


class ReporterAgent(AssistantAgent):
    def __init__(self, name, model_client, tools, description, system_message, max_tool_iterations):
        super().__init__(name=name,
                        model_client=model_client, 
                        tools=tools, 
                        description=description, 
                        system_message=system_message,
                        max_tool_iterations=max_tool_iterations)
    
    @classmethod
    async def create(cls, model_client = None, workspace_folder = None, session_id = None):
        model_client = OllamaClient() if not model_client else model_client
        mcp_server_path = os.path.abspath(
            os.path.join(current_dir, "..", "..", "..", "mcp", "server.py")
        )
        if not os.path.exists(mcp_server_path):
            raise FileNotFoundError(f"MCP server not found at: {mcp_server_path}")
        mcp_server = StdioServerParams(
            command=sys.executable,
            args=[mcp_server_path],
            env={"WORKSPACE_DIR": workspace_folder, "SESSION_ID": session_id, "AGENT_ID": 'latex_reporter_agent'}
            )
        try:
            mcp_tools = await mcp_server_tools(mcp_server)
        except Exception as exc:
            logger.exception("Failed to initialize MCP tools from %s", mcp_server_path)
            raise RuntimeError(f"Failed to initialize MCP tools from {mcp_server_path}") from exc
        desired_tools = ['latex_to_pdf', 'scratchpad_read_report', 'read_all_agent_outputs',
                         'list_latex_templates', 'get_latex_template', 'read_literature_context']
        final_tools = cls.get_desired_tools(mcp_tools, desired_tools)
        # final_tools = mcp_tools
        name = "latex_reporter_agent"
        description = REPORTER_AGENT_DESCRIPTION
        system_message = REPORTER_AGENT_SYSTEM_MESSAGE
        return cls(
            name = name,
            model_client = model_client,
            tools = final_tools,
            description = description,
            system_message = system_message,
            max_tool_iterations = 20
        )
        
    @staticmethod
    def get_desired_tools(mcp_tools, desired_tools):
        return [tool for tool in mcp_tools if tool.name in desired_tools]






async def main():
    # Initialize the agent using the factory method
    agent = await ReporterAgent.create()
    
    # Now you can use the agent normally
    print(f"Agent initialized with {agent._tools} tools.")
    print(agent._max_tool_iterations)
    for tool in agent._tools:
        print(tool.name)
    await Console(
        agent.run_stream(task="make me a pdf for the final report, use the tool provided called latex_to_pdf, you must use it ",
                         cancellation_token=CancellationToken())
        )
if __name__ == "__main__":
    asyncio.run(main())
