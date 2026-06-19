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
from system_prompts.system_messages import POPULATION_PLANNER_AGENT_SYSTEM_MESSAGE, POPULATION_EXEC_SYSTEM_MESSAGE
import asyncio
import logging
import pandas as pd

logger = logging.getLogger()

TASK_CONTEXT_BOOTSTRAP = """=== TASK CONTEXT BOOTSTRAP (MANDATORY) ===
Before doing anything else, call read_agent_task_context exactly once.
Use the returned task_message.instructions and context_augmentation as the primary task specification.
If the tool returns status other than 'ok', raise a scratchpad flag and stop.
"""

import os
import json

def write_inclusion_criteria( criteria: str, modality: str):
    """
    Updates or creates a JSON file with inclusion criteria and modalities.
    """
    json_path = os.path.join(root_dir, "workspace", "populations", "populations", "population.json")
    if os.path.exists(json_path):
        with open(json_path, 'r') as file:
            try:
                data = json.load(file)
            except json.JSONDecodeError:
                data = {} 
    else:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        data = {}

    data['modality'] = modality
    data['criteria'] = criteria
    with open(json_path, 'w') as file:
        json.dump(data, file, indent=4)
        
def get_unique_descriptions():
    csv_path = os.path.join(root_dir, "workspace", "fake_data", "data.csv")
    if os.path.exists(csv_path):
        with open(csv_path, 'r') as file:
            try:
                data = pd.read_csv(csv_path)
            except FileNotFoundError:
                return "file not found"
    return data['series_description'].unique().tolist()   

def search_using_keywords(words_to_include:list, words_to_exclude:list = []):
    """
    perform the final search and writes down the final included cases, when this function terminates correctly then the task is done
    the inputs are the words to include and words to exclude in the cases that will be use in the study population 
    """
    csv_path = os.path.join(root_dir, "workspace", "fake_data", "data.csv")
    json_path = os.path.join(root_dir, "workspace", "populations", "populations", "population.json")
    if os.path.exists(csv_path):
        with open(csv_path, 'r') as file:
            try:
                csv_data = pd.read_csv(csv_path)
            except FileNotFoundError:
                return "file not found"
    descriptions = csv_data['series_description'].tolist()
    # print(descriptions)
    keyword_filtered_results = [description for description in descriptions if any(word in description for word in words_to_include)]
    if len(words_to_exclude):
        keyword_filtered_results = [filtered_result for filtered_result in keyword_filtered_results if any(word not in filtered_result for word in words_to_exclude)]
    
    if os.path.exists(json_path):
        with open(json_path, 'r') as file:
            try:
                data = json.load(file)
            except json.JSONDecodeError:
                data = {} 
    else:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        data = {}
    
    selected_volumes = csv_data[csv_data['series_description'].isin(keyword_filtered_results)]
    selected_volumes_uids = selected_volumes['volume_series_instance_uid'].tolist()
    # print(selected_volumes_uids)
    
    data["datasetlist"] = selected_volumes_uids
    with open(json_path, 'w') as file:
        json.dump(data, file, indent=4)
    return f"number of the {len(keyword_filtered_results)}"
        


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
            read_timeout_seconds=2000
            )
        try:
            mcp_tools = await mcp_server_tools(mcp_server)
        except Exception as exc:
            logger.exception("Failed to initialize MCP tools from %s", mcp_server_path)
            raise RuntimeError(f"Failed to initialize MCP tools from {mcp_server_path}") from exc
        desired_tools = ['get_unique_series_descriptions', 'search_volumes_by_description', 'write_inclusion_criteria', 'generate_targets',
                        'read_agent_task_context',
                        'scratchpad_register_agent', 'scratchpad_raise_flag',
                        'scratchpad_write_summary', 'scratchpad_set_status']
        final_tools = cls.get_desired_tools(mcp_tools, desired_tools)
        logger.info(f"Started population selection with available tools: {len(final_tools)}")
        for tool in final_tools:
            logger.info(f"Tool selected: {tool.name}")
        name = "study_population_planner_agent"
        description = POPULATION_PLANNER_AGENT_DESCRIPTION
        system_message = f"{TASK_CONTEXT_BOOTSTRAP}\n\n{POPULATION_EXEC_SYSTEM_MESSAGE}"
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
        agent.run_stream(task="A radiomics-derived imaging signature from CT imaging can predict weather spleen cancerous or not ",
                         cancellation_token=CancellationToken())
        )
if __name__ == "__main__":
    asyncio.run(main())
    # print(root_dir)
    # csv_path = os.path.join(root_dir, "workspace", "fake_data", "data.csv")
    # data = pd.read_csv(csv_path)
    
    # json_path = os.path.join(root_dir, "workspace", "populations", "populations", "population.json")
    # json_file = pd.read_json(json_path)
    
    # print(get_unique_descriptions(root_dir))
    # print(search_using_keywords(root_dir, ["MR"], []))