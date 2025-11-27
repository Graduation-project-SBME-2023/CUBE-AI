"""
PopulationSelector  defines patient inclusion / exclusion criteria.

This agent is responsible for:
* Receiving a clinical hypothesis and any demographic constraints.
* Producing a structured list of inclusion and exclusion rules that can be
  consumed by downstream modules (e.g., imaging protocol design, recruitment).
  
  It also Given a hypothesis and population criteria, this agent proposes the
appropriate imaging modality, acquisition parameters, and any required
contrast agents.
"""
from autogen_ext.models.ollama import OllamaChatCompletionClient
import asyncio
from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_core import CancellationToken
from pydantic import BaseModel
from typing import List, Tuple, Union, Dict, Any
from typing import Dict, Any
import requests
import datetime
import json
import asyncio

from pathlib import Path

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
        
        current_dir = Path(__file__).resolve().parent
        json_path = current_dir.parent / "memory" / "workspace" / "dataset.json"
        file_name = json_path      
        

    # Ensure it's serializable
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    return {"status": "success", "file": json_path}


class IncludedData(BaseModel):
    modality: Union[str, List[str]]
    gender: Union[str, List[str]]
    age: Tuple[int, int]
    patient_ids: Union[str, List[str]]
    study_uids: Union[str, List[str]]
    model_config = {
        "title": "IncludedData",
        "description": "use this model if you have found cases to use in your population"
    }
    


class PopulationSelector:
    """Placeholder for the population‑selection module."""

    def __init__(self):
        current_dir = Path(__file__).resolve().parent
        config_path = current_dir.parent / "memory" / "workspace" / "state.json"
                 
        self.config_path = config_path
        self.rules: Dict[str, Any] = {}
        self.model_client = OllamaChatCompletionClient(
        model="gpt-oss:120b-cloud",
        model_info={
            "vision": False,
            "function_calling": True,
            "json_output": True,
            "structured_output": True
        }
    )

    async def initialize(self) -> bool:
        """
        Perform any one‑time setup (e.g., load reference tables).
        Returns ``True`` when ready.
        """
        # TODO: load real population data / reference tables
        return True

    async def run(self):
        """
        Generate inclusion / exclusion criteria from a hypothesis.

        Parameters
        ----------
        hypothesis: dict
            Structured hypothesis (output of the Planner).

        Returns
        -------
        dict
            ``{"inclusion": [...], "exclusion": [...]}``
        """

        agent = AssistantAgent(
        name="radiologist",
        model_client=self.model_client,
        tools=[get_all_patients_info, get_all_studies_info, save_final_output],
        max_tool_iterations= 5,
        system_message="""You are the study population selector.you will be given a hypothesis and you will use 
        the tools provided to get all the information about the patients and studies and then 
        you need to select what is the population that will be used to continue the study.at the end you need to call the 
        tool for the save final output and add on its arguments output with the shape of the 
        class IncludedData(BaseModel):
        modality: Union[str, List[str]]
        gender: Union[str, List[str]]
        age: Tuple[int, int]
        patient_ids: Union[str, List[str]]
        study_uids: Union[str, List[str]]You are the study population selector.you will be given a hypothesis and you will use 
        the tools provided to get all the information about the patients and studies and then 
        you need to select what is the population that will be used to continue the study.at the end you need to call the 
        tool for the save final output and add on its arguments output with the shape of the 
        class IncludedData(BaseModel):
        modality: Union[str, List[str]]
        gender: Union[str, List[str]]
        age: Tuple[int, int]
        patient_ids: Union[str, List[str]]
        study_uids: Union[str, List[str]]
        """,
        )

    # # Let the agent translate some text
    #     await Console(
    #     agent.run_stream(task="A radiomics-derived imaging signature from CT imaging can predict high-risk patients with neuroblastoma",
    #                      cancellation_token=CancellationToken())
    #     )

        # Placeholder logic – just echo back empty lists
        self.rules = {"inclusion": [], "exclusion": []}
        return self.rules


__all__ = ["PopulationSelector"]


async def main():
    pop = PopulationSelector()
    await pop.run()  # also needs await if run() is async

asyncio.run(main())