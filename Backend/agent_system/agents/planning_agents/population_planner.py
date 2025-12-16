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


class PopulationPlannerAgent(AssistantAgent):
    def __init__(self, name, model_client, tools, description, system_message, max_tool_iterations):
        super().__init__(name=name,
                        model_client=model_client, 
                        tools=tools, 
                        description=description, 
                        system_message=system_message,
                        max_tool_iterations=max_tool_iterations)
    
    @classmethod
    async def create(cls, model_client = None):
        model_client = OllamaClient() if not model_client else model_client
        mcp_server = StdioServerParams(
            command="python", args=["Backend/mcp/server.py"]
            )
        mcp_tools = await mcp_server_tools(mcp_server)
        final_tools = mcp_tools
        name = "study_population_planner_agent"
        description = POPULATION_PLANNER_AGENT_DESCRIPTION
        system_message = POPULATION_PLANNER_AGENT_SYSTEM_MESSAGE
        return cls(
            name = name,
            model_client = model_client,
            tools = final_tools,
            description = description,
            system_message = system_message,
            max_tool_iterations = 20
        )


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
if __name__ == "__main__":
    asyncio.run(main())
