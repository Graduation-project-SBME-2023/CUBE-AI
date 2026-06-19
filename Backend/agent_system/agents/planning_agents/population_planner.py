import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
sys.path.append(root_dir)

from pathlib import Path
from autogen_agentchat.agents import AssistantAgent
from clients.ollamaClient import OllamaClient
from autogen_core.models import SystemMessage
from autogen_agentchat.ui import Console
from autogen_core import CancellationToken
from autogen_ext.tools.mcp import mcp_server_tools, StdioServerParams
from descriptions.models_descriptions import POPULATION_PLANNER_AGENT_DESCRIPTION
from system_prompts.system_messages import POPULATION_PLANNER_AGENT_SYSTEM_MESSAGE
import asyncio
import logging
import json

logger = logging.getLogger('Population Planner')


class PopulationPlannerAgent(AssistantAgent):
    def __init__(self, name, model_client, tools, description, system_message, max_tool_iterations):
        super().__init__(name=name,
                        model_client=model_client, 
                        tools=tools, 
                        description=description, 
                        system_message=system_message,
                        max_tool_iterations=max_tool_iterations)
    
    @classmethod
    async def create(cls, model_client = None, workspace_folder=None, session_id=None):
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
            "AGENT_ID": "study_population_planner_agent"
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
            raise RuntimeError(f"Failed to initialize MCP tools from {mcp_server_path}") from exc
        desired_tools = ['generate_targets', 'get_patient', 'get_volume', 'save_final_output',
                         'scratchpad_register_agent', 'scratchpad_raise_flag',
                         'scratchpad_write_summary', 'scratchpad_set_status',
                 'write_agent_output', 'generate_initial_latex']
        final_tools = cls.get_desired_tools(mcp_tools, desired_tools)
        logger.info(f"Started population selection with available tools: {len(final_tools)}")
        for tool in final_tools:
            logger.info(f"Tool selected: {tool.name}")
        name = "study_population_planner_agent"
        description = POPULATION_PLANNER_AGENT_DESCRIPTION
        system_message = POPULATION_PLANNER_AGENT_SYSTEM_MESSAGE
        instance = cls(
            name = name,
            model_client = model_client,
            tools = final_tools,
            description = description,
            system_message = system_message,
            max_tool_iterations = 20
        )
        # expose workspace and session for helper methods
        instance._workspace_folder = workspace_folder
        instance._session_id = session_id
        return instance

    async def generate_initial_report(self, sections: str = "introduction,literature_review") -> str:
        """Generate and compile the initial LaTeX document using the planning MCP tool.

        This helper calls the internal MCP tool implementation directly so the
        planner can generate the initial report at confirmation time.
        Returns the tool JSON string result.
        """
        try:
            # Dynamically load the MCP tool module from the mcp tools folder to
            # avoid import path issues in different runtimes.
            import importlib.util
            tool_path = os.path.abspath(os.path.join(current_dir, "..", "..", "..", "mcp", "tools", "planning", "generate_initial_latex.py"))
            spec = importlib.util.spec_from_file_location("generate_initial_latex_tool", tool_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            generate_initial = getattr(module, "generate_initial_latex")

            result = await generate_initial(sections)
            return result
        except Exception as e:
            logger.exception("Failed to generate initial report: %s", e)
            return json.dumps({"status": "error", "message": str(e)})
    
    @staticmethod
    def get_desired_tools(mcp_tools, desired_tools):
        for tool in mcp_tools:
            logger.info(f"Tool available: {tool.name}")
        return [tool for tool in mcp_tools if tool.name in desired_tools]


import asyncio

async def main():
    # Initialize the agent using the factory method
    agent = await PopulationPlannerAgent.create()
    
    # Now you can use the agent normally
    print(f"Agent initialized with {len(agent._tools)} tools.")
    await Console(
        agent.run_stream(task="A radiomics-derived imaging signature from CT imaging can predict high-risk patients with neuroblastoma ",
                         cancellation_token=CancellationToken())
        )
# if __name__ == "__main__":
#     asyncio.run(main())
