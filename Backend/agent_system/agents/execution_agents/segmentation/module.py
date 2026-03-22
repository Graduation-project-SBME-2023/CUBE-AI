"""
SegmentationModule – automates ROI segmentation.

The module receives an imaging protocol and raw volumes, then runs a
segmentation model (e.g., nnUNet, MONAI) to produce binary masks for the
structure
"""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Union, List
from autogen_ext.models.ollama import OllamaChatCompletionClient
from .planner import create_planner_agent, run_planner
from .pipelines.automatic import run_automatic_pipeline
from .pipelines.interactive import run_interactive_pipeline, create_interactive_agent, create_user_proxy_agent
from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools
from .services import SegmentationService
from .utils import exclude_existing_masks, download_and_pair_volumes
from .logging_config import setup_logging
import logging

setup_logging()

logger = logging.getLogger("SegmentationModule")

class SegmentationModule:

    def __init__(self, config_path: str = "config.json", model_name: str = "gpt-oss:120b-cloud"):

        self.config_path = config_path
        self.model_name = model_name
        self.model_client = None  
        self.PlannerAgent = None
        self.UserProxyAgent = None
        self.UserFallbackAgent = None
        self.segmentation_service = None


    async def initialize(self):
        # intialize model client
        self.model_client = OllamaChatCompletionClient(
        model=self.model_name,
        model_info={
            "vision": False,
            "function_calling": True,
            "json_output": True,
            "structured_output": True,
            "family": "unknown"
        },
        api_key=os.environ.get("APIKEY")
     )
        # MCP server connection
        current_file = Path(__file__).resolve()
        backend_dir = current_file.parent.parent.parent.parent
        server_path = backend_dir / "mcp" / "server.py"   
        logger.info(f"Starting MCP server at: {server_path}")     
        math_server = StdioServerParams(
            command="python", args=[str(server_path)], timeout=None
        )
        try:
            tools = await mcp_server_tools(math_server)
            logger.info(f"Connected! Available tools count: {len(tools)}")
            
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            raise 
        
        self.discovery_tools = [t for t in tools if t.name == "get_model_info"]
        assert self.discovery_tools, "Missing get_model_info" 
        
        # intialize agents and services
        self.PlannerAgent = create_planner_agent(self.model_client, self.discovery_tools)
        self.UserFallbackAgent = create_interactive_agent(self.model_client, self.discovery_tools)
        self.UserProxyAgent = create_user_proxy_agent(self.model_client)
        self.segmentation_service = SegmentationService()


    async def run(self, input_data: Union[str, Dict[str, Any]]):
        if isinstance(input_data, str):
            with open(input_data, "r") as f:
                input_data = json.load(f)

        hypothesis: str = input_data["hypothesis"]
        datasetlist: List[str] = input_data["datasetlist"]

        # plan
        plan = await run_planner(self.PlannerAgent,hypothesis)
        logger.info(f"Planner plan: {plan}")
        # target_labels = plan["target_labels"]
        # chosen_path = plan["chosen_path"]
        # eligible_volumes = await exclude_existing_masks(
        #     datasetlist, target_labels
        # )

        # logger.info(
        #     f"Eligible volumes for segmentation: {len(eligible_volumes)}"
        # )
        # chosen_path = "automatic"
        # eligible_volumes = datasetlist
        # target_labels = ["spleen"]
        # selected_auto_model= "segmentation_spleen" # hard test

        # whether it's automatic or interactive
        # if chosen_path == "automatic" and eligible_volumes:
        #     selected_auto_model = plan["automatic_models"][0]
        #     # selected_auto_model = "segmentation_spleen" # hard test
        #     await run_automatic_pipeline(eligible_volumes, target_labels, selected_auto_model, self.segmentation_service)

        # elif eligible_volumes:
        #     await run_interactive_pipeline(self.UserFallbackAgent, self.UserProxyAgent, target_labels)
            
        # finally download the volumes with its label masks 
        # await asyncio.sleep(120)  
        # await download_and_pair_volumes(
        #         datasetlist, 
        #         chosen_path=chosen_path, 
        #         selected_auto_model_for_volume=plan["automatic_models"][0] if plan["automatic_models"] else None
        #     )

        return {"status": "finished"}
    
# for testing the agent alone 
async def test_module():
    """Test function for the SegmentationModule"""
    try:
        logger.info("Starting SegmentationModule test...")
        
        agent = SegmentationModule()
        await agent.initialize()
        
        current_file = Path(__file__)
        config_path =os.path.join(current_file.parent.parent.parent.parent,"mas","memory", "workspace", "configurations.json")
        logger.info(f"Running with config: {config_path}")
        
        result = await agent.run(config_path)
        
        logger.info("Test completed successfully!")
        logger.info(f"Result: {result}")
        
    except Exception as e:
        logger.error(f"Test failed with error: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(test_module())

__all__ = ["SegmentationModule"]