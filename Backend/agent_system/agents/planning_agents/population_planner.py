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
from system_prompts.system_messages import POPULATION_PLANNER_AGENT_SYSTEM_MESSAGE
import requests
import json 
import asyncio


class PopulationPlannerAgent(AssistantAgent):
    def __init__(self, model_client = None):
        name = "study_population_planner_agent"
        model_client = OllamaClient() if not model_client else model_client
        tools = [ get_all_studies_info, save_final_output]
        description = POPULATION_PLANNER_AGENT_DESCRIPTION
        system_message = POPULATION_PLANNER_AGENT_SYSTEM_MESSAGE
        super().__init__(name=name, model_client=model_client, tools=tools, description=description, system_message=system_message )
        current_dir = Path(__file__).resolve().parent
        self.config_path = current_dir.parent / "memory" / "workspace" / "state.json"
    
    
    
# TODO: remove the tools and use the mcp server


async def get_all_studies_info():
    """
    This tool returns all the studies info that could be used in 
    specifying the population that is targeted in the study
    """
    response = requests.get("http://localhost:8000/studies")
    return response.json()

async def get_all_patients_info():
    """
    This tool returns all the patients info that could be used in 
    specifying the population that is targeted in the study
    """
    response = requests.get("http://localhost:8000/patients")
    return response.json()

async def save_final_output(data: dict, filename: str = None):
    """
    Save the agent's final output to a JSON file.
    
    Arguments:
        data -- A Python dict containing the agent's final output.
        filename -- Optional filename. If not provided, a timestamped one will be created.
    
    Returns:
        A confirmation message with the saved file path.
    """
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        current_dir = Path(__file__).resolve().parent.parent
        json_path = current_dir.parent / "data" / "populations" / "dataset.json"
        file_name = json_path      
        

    # Ensure it's serializable
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    return {"status": "success", "file": json_path}


async def test():
    test_agent = PopulationPlannerAgent()
    await Console(
        test_agent.run_stream(task="A radiomics-derived imaging signature from CT imaging can predict high-risk patients with neuroblastoma ",
                         cancellation_token=CancellationToken())
        )
    
asyncio.run(test())



# print(Path(__file__).resolve().parent.parent.parent)

