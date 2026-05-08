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
import httpx
from pathlib import Path
from typing import Dict, Any, Union, List
from autogen_ext.models.ollama import OllamaChatCompletionClient
from .planner import create_planner_agent, run_planner
from .pipelines.automatic import run_automatic_pipeline
from .pipelines.interactive import run_interactive_pipeline, create_interactive_agent, create_user_proxy_agent
from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools
from .services import SegmentationService
from .utils import exclude_existing_masks, download_and_pair_volumes, generate_gif_overlays, cleanup_discarded, get_model_dice_score
from .logging_config import setup_logging
import logging
from client_config import create_model_client_from_config
import httpx
from agent_system.models.tool_logging import ProgressPayload, TextPayload, ToolEvent
from agent_system.tool_logging import receive_tool_event
setup_logging()

logger = logging.getLogger("SegmentationModule")

class SegmentationModule:

    def __init__(self, config_path: str = "config.json", model_name: str = "gpt-oss:120b-cloud", session_id: str = None):

        self.config_path = config_path
        self.model_name = model_name
        self.session_id = session_id
        self.model_client = None  
        self.PlannerAgent = None
        self.UserProxyAgent = None
        self.UserFallbackAgent = None
        self.segmentation_service = None


    async def initialize(self):
        # intialize model client
        self.model_client = create_model_client_from_config()
        logger.info(f"Model client initialized with : {self.model_client}")

        # MCP server connection
        current_file = Path(__file__).resolve()
        backend_dir = current_file.parent.parent.parent.parent.parent
        server_path = backend_dir / "mcp" / "server.py"   
        logger.info(f"Starting MCP server at: {server_path}, with session id: {self.session_id}")
        math_server = StdioServerParams(
            command="python", args=[str(server_path)], timeout=None, env={"SESSION_ID": self.session_id, "AGENT_ID": 'segmentation_agent'}
        )
        try:
            tools = await mcp_server_tools(math_server)
            logger.info(f"Connected! Available tools count: {len(tools)}")
            
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            raise 
        
        self.discovery_tools = [t for t in tools if t.name == "list_models"]
        assert self.discovery_tools, "Missing list_models" 
        
        # intialize agents and services
        self.PlannerAgent = create_planner_agent(self.model_client, self.discovery_tools)
        logger.info("PlannerAgent initialized.")
        self.UserFallbackAgent = create_interactive_agent(self.model_client, self.discovery_tools)
        self.UserProxyAgent = create_user_proxy_agent(self.model_client)
        self.segmentation_service = SegmentationService()


    async def run(self, input_data: Union[str, Dict[str, Any]], workspace_dir: str):
        # input data is config_path
        if isinstance(input_data, str):
            state_path = input_data.replace('config', 'state')
            with open(state_path, 'r') as f:
                state = json.load(f)
            with open(input_data, "r") as f:
                input_data = json.load(f)
        steps = state.get("plan", {}).get("steps", [])

        segmentation_step = next(
            (s for s in steps if s.get("module") == "segmentation_agent"),
            None
        )

        if not segmentation_step:
            raise ValueError("Segmentation step not found")

        instructions = segmentation_step["task_message"]["instructions"]
        logger.info(f"Received instructions: {instructions}")
        datasetlist: List[str] = input_data["datasetlist"]
        #1. plan
        plan = await run_planner(self.PlannerAgent,instructions)
        logger.info(f"Planner plan: {plan}")
        target_labels = plan["target_labels"]
        chosen_path = plan["chosen_path"]
        eligible_volumes = await exclude_existing_masks(
            datasetlist, target_labels
        )
        # ====== Logging before Segmentation =================
        payload = TextPayload(
            message=f'Found existing masks for {len(datasetlist) - len(eligible_volumes)} volumes'
        )
        plan_log_event = ToolEvent(
            event_type='tool_started',
            source='segmentation_agent',
            content_type='text',
            content=payload,
            session_id=self.session_id,
        )
        await receive_tool_event(plan_log_event)
        plan_log_event = ToolEvent(
            event_type='tool_finished',
            source='segmentation_agent',
            content_type='text',
            content=payload,
            session_id=self.session_id,
        )
        await receive_tool_event(plan_log_event)
        
        # =========== test ===============

        # plan ={'target_labels': ['Spleen'], 'chosen_path': 'automatic','selected_models': ['segmentation_spleen']}
        # =================================
        
        logger.info(
            f"Eligible volumes for segmentation: {len(eligible_volumes)}"
        )
        #whether it's automatic or interactive
        if chosen_path == "automatic" and eligible_volumes:
            selected_auto_models = plan["selected_models"] if plan["selected_models"] else None
            await run_automatic_pipeline(eligible_volumes, target_labels, selected_auto_models, self.segmentation_service, self.session_id)

        elif eligible_volumes:
            selected_interactive_model = await run_interactive_pipeline(self.UserFallbackAgent, self.UserProxyAgent, eligible_volumes, target_labels, self.segmentation_service, self.session_id)
        # ========= logging after Segmentation ================
        payload = TextPayload(message=f'Segmentation...Done')
        segmentation_tool_event = ToolEvent(
            event_type='tool_finished',
            source='segmentation_agent',
            content_type='text',
            content=payload,
            session_id=self.session_id,
        )
        await receive_tool_event(segmentation_tool_event)  
        
        # ===========  loggin before download ====================  
        payload = TextPayload(message='Creating dataset...')
        environment_setup_event = ToolEvent(
            event_type='tool_started',
            source='segmentation_agent',
            content_type='text',
            content=payload,
            session_id=self.session_id,
        )
        await receive_tool_event(environment_setup_event)
        environment_setup_event = ToolEvent(
            event_type='tool_progress',
            source='segmentation_agent',
            content_type='text',
            content=payload,
            session_id=self.session_id,
        )
        await receive_tool_event(environment_setup_event)
        # =========== download ========================    
        #finally download the volumes with its label masks 
        await asyncio.sleep(120)  
        downloaded_uids = await download_and_pair_volumes(
                    workspace_dir,
                    datasetlist, 
                    chosen_path=chosen_path, 
                    selected_auto_model_for_volume = plan["selected_models"][0] if plan["selected_models"] else None
        )
        
        #============================ test segmentation view ===================================
        # downloaded_uids = ["1.2.826.0.1.3680043.8.498.15136386566608168882487015520907740870"] 
        # ======================================================================================
        
        # ================== Generate Overlays ===========================================
        gif_volumes = []
        if downloaded_uids:
            
            gif_volumes = await asyncio.to_thread(
                generate_gif_overlays, 
                workspace_dir, downloaded_uids
            )
            logger.info(f"gif_volumes sample: {gif_volumes[:1]}")

        #=========== Prepare data for the frontend(payload)==============================
        #todo: handle list of labels and models in front view and in function
        frontend_data = {
            "targetRoi": plan['target_labels'][0], 
            "selectedModel": plan["selected_models"][0] if plan["selected_models"] else None, 
            "diceScore": get_model_dice_score(plan["selected_models"][0],plan["target_labels"][0]) if plan["selected_models"] else None, 
            "volumes": gif_volumes
        }

        # ==========sending data to fronted for review====================
        logger.info("Module sending data to frontend for review...")
        async with httpx.AsyncClient(timeout=60.0) as client:
            await client.post(
                "http://localhost:8150/ws-broadcast",
                json={
                    "type": "segmentation_output",
                    "payload": frontend_data,
                    "session_id": self.session_id
                }
            )
  
        # Wait for user review message
        logger.info("Module paused: Waiting for human review via HTTP long-poll...")
        async with httpx.AsyncClient(timeout=330.0) as client:
                    while True:
                        try:
                            response = await client.get(
                                f"http://localhost:8150/segmentation-review/wait/{self.session_id}"
                            )
                            
                            # if time out reconnect
                            if response.status_code == 408:
                                logger.info("Server ended the long-poll (408 Timeout). Reconnecting...")
                                continue  
                            # for other errors, log and raise
                            response.raise_for_status()
                            # successful response, break the loop and process results
                            user_results = response.json()
                            break 
                        except httpx.ReadTimeout:
                            logger.info("Client read timeout reached. Reconnecting...")
                            continue

        final_accepted_uids = user_results.get("accepted", [])
        final_rejected_uids = user_results.get("rejected", [])
        final_modified_uids = user_results.get("modified", [])
        logger.info(f"Module resumed! Accepted: {final_accepted_uids}, Rejected: {final_rejected_uids}, Modified: {final_modified_uids}")
        
        # #============= accepted and modified uids just kept only in the folder=================
        cleanup_discarded(workspace_dir, final_accepted_uids, final_modified_uids)
        if final_modified_uids:
            logger.info("Fetching updated labels for modified UIDs...")
            await download_and_pair_volumes(
                workspace_dir, 
                datasetlist=final_modified_uids, 
                chosen_path="interactive", 
                selected_auto_model_for_volume=None, 
                download_volumes=False 
            )


        return {"status": "finished"}
    
# for testing the agent alone 
async def test_module():
    """Test function for the SegmentationModule"""
    try:
        logger.info("Starting SegmentationModule test...")
        
        agent = SegmentationModule(session_id="68dac4a1-ecdb-4145-9f23-874a85d1d6bb")
        await agent.initialize()
        
        current_file = Path(__file__)
        workspace_folder = os.path.join(current_file.parent.parent.parent.parent,"workspace_68dac4a1-ecdb-4145-9f23-874a85d1d6bb")
        config_path = os.path.join(workspace_folder, "config.json")
        result = await agent.run(config_path, workspace_folder)
        
        logger.info("Test completed successfully!")
        logger.info(f"Result: {result}")
        
    except Exception as e:
        logger.error(f"Test failed with error: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(test_module())

__all__ = ["SegmentationModule"]
