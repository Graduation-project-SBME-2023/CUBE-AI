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
from descriptions.models_descriptions import POPULATION_PLANNER_AGENT_DESCRIPTION
from system_prompts.system_messages import REPORTER_AGENT_SYSTEM_MESSAGE
import requests
import json 
import asyncio


class ReporterAgent(AssistantAgent):
    def __init__(self, model_client = None):
        name = "reporter_agent"
        model_client = OllamaClient() if not model_client else model_client
        tools = []
        description = POPULATION_PLANNER_AGENT_DESCRIPTION
        system_message = REPORTER_AGENT_SYSTEM_MESSAGE
        super().__init__(name=name, model_client=model_client, tools=tools, description=description, system_message=system_message )
        current_dir = Path(__file__).resolve().parent
        self.config_path = current_dir.parent / "memory" / "workspace" / "state.json"
    
    
    
# TODO: remove the tools and use the mcp server
