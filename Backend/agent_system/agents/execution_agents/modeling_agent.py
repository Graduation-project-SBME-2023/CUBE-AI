import asyncio
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
sys.path.append(root_dir)

import os
import json
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Union, List, Dict, Any, Optional
import ast

from pathlib import Path
from autogen_agentchat.agents import AssistantAgent
from clients.ollamaClient import OllamaClient
from autogen_core.models import SystemMessage
from autogen_agentchat.ui import Console
from autogen_core import CancellationToken
from descriptions.models_descriptions import MODELING_AGENT_DESCRIPTION
from system_prompts.system_messages import MODELING_AGENT_SYSTEM_PROMPT
from autogen_ext.tools.mcp import mcp_server_tools, StdioServerParams
from tools.classification_tool import classification_modelling
from autogen_core.tools import FunctionTool


modelling_classification_tool = classification_modelling


class ModelingAgent(AssistantAgent):
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
        # desired_tools = []
        # final_tools = cls.get_desired_tools(mcp_tools, desired_tools)
        # final_tools = mcp_tools
        modelling_classification_tool_wrapper = FunctionTool(
            func=classification_modelling,
            description="""
            Asynchronously executes the full Classification Modelling pipeline.
            this tool have the capability internally to get the results of the feature extraction and work on it
            just call it when you need to start the feature extraction
            Use this tool in scenarios such as:
            1. When you need to train multiple machine learning algorithms on a dataset and automatically select the best one.
            2. To generate a complete scientific report containing model metrics, confusion matrices, and ROC curves.
            3. When you need to perform advanced data preprocessing (normalization, balancing) as part of the training workflow.
            """  ,
            name="classification_modelling"
        )
        
        name = "modeling_agent"
        description = MODELING_AGENT_DESCRIPTION
        system_message = MODELING_AGENT_SYSTEM_PROMPT
        return cls(
            name = name,
            model_client = model_client,
            tools = [modelling_classification_tool_wrapper],
            description = description,
            system_message = system_message,
            max_tool_iterations = 20
        )
        
    @staticmethod
    def get_desired_tools(mcp_tools, desired_tools):
        return [tool for tool in mcp_tools if tool.name in desired_tools]

# async def main():
#     # Initialize the agent using the factory method
#     agent = await ModelingAgent.create()
    
#     # Now you can use the agent normally
#     print(f"Agent initialized with {agent._tools} tools.")
#     print(agent._max_tool_iterations)
#     for tool in agent._tools:
#         print(tool.name)
#     await Console(
#         agent.run_stream(task="run the classification modeling, provide as argument to the tool just experiment name, make the name experiment1",
#                          cancellation_token=CancellationToken())
#         )
# if __name__ == "__main__":
#     asyncio.run(main())