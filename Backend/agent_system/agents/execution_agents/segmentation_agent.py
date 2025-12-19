import asyncio
import sys
import os

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
# from autogen_ext.tools.mcp import mcp_server_tools, StdioServerParams
from mas.agents.segmentation_agent import SegmentationModule
from autogen_core.tools import FunctionTool


async def test_module():
    """Test function for the SegmentationModule with return value"""
    try:
        # print("Starting SegmentationModule test...")
        
        # Initialize
        agent = SegmentationModule()
        await agent.initialize()
        
        current_file = Path(__file__)
        config_path = os.path.join(current_file.parent.parent.parent.parent, "mas", "memory", "workspace", "configurations.json")
        # print(f"Running with config: {config_path}")
        
        # Run the agent and capture the result
        result = await agent.run(config_path)
        
        # print("Test completed successfully!")
        # print(f"Result: {result}")
        
        return result
        
    except Exception as e:
        # print(f"Test failed with error: {e}", exc_info=True)
        raise e



class SegmentationAgent(AssistantAgent):
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
        # mcp_server = StdioServerParams(
        #     command="python", args=["Backend/mcp/server.py"]
        #     )
        # mcp_tools = await mcp_server_tools(mcp_server)
        # desired_tools = ['get_modeling_results']
        # final_tools = cls.get_desired_tools(mcp_tools, desired_tools)
        # final_tools = mcp_tools
        tools = [
            FunctionTool(
                test_module,
                description="the tool that starts the segmentation, it has the capability to know what to segment and install by itself"
            )
        ]
        name = "segmentation_agent"
        description = "agent to run the segmentation task, has the capability to know which volumes to segment by itself"
        system_message = "to start the task of the segmentation, all you need to do just run the tool you have, do not ask any questions"
        return cls(
            name = name,
            model_client = model_client,
            tools = tools,
            description = description,
            system_message = system_message,
            max_tool_iterations = 2
        )
        
    @staticmethod
    def get_desired_tools(mcp_tools, desired_tools):
        return [tool for tool in mcp_tools if tool.name in desired_tools]

# async def main():
#     # Initialize the agent using the factory method
#     agent = await SegmentationAgent.create()
    
#     # # Now you can use the agent normally
#     # print(f"Agent initialized with {agent._tools} tools.")
#     # print(agent._max_tool_iterations)
#     # for tool in agent._tools:
#     #     print(tool.name)
#     await Console(
#         agent.run_stream(task="run segmentation",
#                          cancellation_token=CancellationToken())
#         )
# if __name__ == "__main__":
#     asyncio.run(main())
