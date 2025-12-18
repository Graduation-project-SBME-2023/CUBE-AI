"""
SegmentationModule – automates ROI segmentation.

The module receives an imaging protocol and raw volumes, then runs a
segmentation model (e.g., nnUNet, MONAI) to produce binary masks for the
structures of interest.
"""
from pathlib import Path
import shutil
import sys
import time
from typing import Dict, Any, Union, List
from autogen_ext.models.ollama import OllamaChatCompletionClient
import asyncio
from autogen_ext.tools.mcp import StdioServerParams, mcp_server_tools
from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.ui import Console
from autogen_core import CancellationToken
from autogen_agentchat.teams import SelectorGroupChat
from autogen_agentchat.conditions import TextMentionTermination
from autogen_agentchat.base import TaskResult
import json
import logging
from dotenv import load_dotenv
import os
import requests

load_dotenv() 
log_file_path = Path(__file__).parent.parent/"memory"/"workspace"/ "segmentation_module.log"
logger = logging.getLogger("SegmentationModule")
logger.setLevel(logging.INFO)
formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
# console handler
ch = logging.StreamHandler()
ch.setFormatter(formatter)
logger.addHandler(ch)
# file handler
fh = logging.FileHandler(log_file_path)
fh.setFormatter(formatter)
logger.addHandler(fh)

class SegmentationModule:
    """
    Implements the SEGMENTATION Team Pipeline using explicit agents and a SelectorGroupChat team.
    Handles automatic and interactive segmentation workflows.
    """
    def __init__(self, config_path: str = "config.json", model_name: str = "gpt-oss:120b-cloud"):
        self.config_path = config_path
        self.model_name = model_name
        self.model_client = None
        self.tools = None
        self.discovery_tool = None
        self.execution_tools = None
        self.PlannerAgent = None
        self.ExecutionAgent = None
        self.UserFallbackAgent = None
        self.team = None

    async def initialize(self):
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
        current_file = Path(__file__).resolve()
        backend_dir = current_file.parent.parent.parent  
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
        self.execution_tools = [
            t for t in tools
            if t.name in ("check_having_mask", "run_segmentation")
        ]
        assert self.discovery_tools, "Missing get_model_info"
        assert self.execution_tools, "Missing execution tools"        
        self._init_agents()
        return True
    

    def _init_agents(self):
        self.PlannerAgent = AssistantAgent(
            name="PlannerAgent",
            model_client=self.model_client,
            tools=self.discovery_tools,
            system_message="""
                   You are PlannerAgent.
                Your responsibilities:
                1. Infer target anatomical label(s) from the hypothesis.
                2. Use  tool to discover available segmentation models.
                3. Analyze the returned models 
                4. Decide whether segmentation should be automatic or interactive based on available models.
                After calling the tool and receiving the model information, you MUST analyze the results and output ONLY a JSON object (no other text) that STRICTLY follows this schema:

                {
                "target_labels": ["label1", "label2"],
                "chosen_path": "automatic",
                "automatic_models": ["model1", "model2"],
                "interactive_models": ["model3"],
                "fallback_path": "interactive",
                "exclusion_rule": "exclude_existing_masks_for_same_label"
                }

                Rules:
                - target_labels: Extract anatomical structures from the hypothesis (e.g., "spleen", "liver")
                - chosen_path: "automatic" if automatic models exist for the target labels, otherwise "interactive"
                - automatic_models: List models where type contains "automatic" and match target labels
                - interactive_models: List models where type contains "interactive"
                - fallback_path: "interactive" if automatic fails, "none" if not applicable
                - Output ONLY valid JSON, no explanations or markdown

                    """,
                reflect_on_tool_use= True
                # max_tool_iterations=2
        )
        self.ExecutionAgent = AssistantAgent(
            name="ExecutionAgent",
            model_client=self.model_client,
            tools=self.execution_tools,
            system_message="""
            You are ExecutionAgent.
            You operate on exactly one volume at a time.
            CRITICAL: Read the task carefully to understand which tool to use.
            If the task says "check" or "check_having_mask":
            - Use ONLY check_having_mask tool to check if a mask exists for the given volume and label.
            - Respond with ONLY a JSON object: {"mask_exists": true} ONLY if the mask is for the needed label or {"mask_exists": false} if no mask or having mask but with different label.
            - DO NOT run segmentation
            If the task says "run" or "run_segmentation":
            - Use ONLY run_segmentation tool
            - Execute the segmentation with provided parameters
                """,
                max_tool_iterations= 2
                        )   
        self.UserFallbackAgent = AssistantAgent(
            name="UserFallbackAgent",
            model_client=self.model_client,
            tools=self.discovery_tools,
            system_message="""
                You are UserFallbackAgent.

                Your role:
                - Explain available interactive segmentation models.
                - Explain their parameters using discovery tools.
                - Guide the user step-by-step.
                - Answer user questions.

                Rules:
                - You MUST NOT run segmentation or check masks.
                - You MUST wait for user confirmation via UserProxy.
                - Terminate only when user confirm that the task is done.
                """,
                reflect_on_tool_use= True,
                max_tool_iterations=3 # need to optimize
                
        )

        self.UserProxy = UserProxyAgent(name="HumanUser")

    async def run(self, input_data: Union[str, Dict[str, Any]]):
        if isinstance(input_data, str):
            with open(input_data, "r") as f:
                input_data = json.load(f)

        hypothesis: str = input_data["hypothesis"]
        datasetlist: List[str] = input_data["datasetlist"]

        # plan
        plan = await self._run_planner(hypothesis)
        logger.info(f"Planner plan: {plan}")
        target_labels = plan["target_labels"]
        chosen_path = plan["chosen_path"]
        # target_labels=['spleen'] # hard test
        # chosen_path='automatic' # hard test
        # eligible_volumes=datasetlist # hard test
        # exclude existing masks
        eligible_volumes = await self._exclude_existing_masks(
            datasetlist, target_labels
        )

        logger.info(
            f"Eligible volumes for segmentation: {len(eligible_volumes)}"
        )

        # whether it's automatic or interactive
        if chosen_path == "automatic" and eligible_volumes:
            # selected_auto_model = "segmentation_spleen" # hard test
            selected_auto_model = plan["automatic_models"][0]
            await self._run_automatic(eligible_volumes, target_labels, selected_auto_model)

        else:
            await self._run_interactive(target_labels)
            
        # finally download the volumes with its label masks 
        # selected_auto_model_for_volume= "segmentation_spleen" # hard test
        await asyncio.sleep(120)  
        await self.download_and_pair_volumes(
                datasetlist, 
                chosen_path=chosen_path, 
                selected_auto_model_for_volume=plan["automatic_models"][0] if plan["automatic_models"] else None
            )

        return {"status": "finished"}
    
    async def _run_planner(self, hypothesis: str) -> Dict[str, Any]:
        task = f"""
            Analyze this hypothesis:

            {hypothesis}

            Steps:
            1.  discover available models
            2. Identify the target anatomical label(s) from the hypothesis
            3. Categorize models as automatic or interactive
            4. Determine if automatic models exist for the target labels
            5. Output the JSON plan following the required schema
            Remember: Output ONLY the JSON object, nothing else..
            """

        result = await self.PlannerAgent.run(task=task)
        return json.loads(result.messages[-1].content)
    

    async def _exclude_existing_masks(
        self, volumes: List[str], labels: List[str]
    ) -> List[str]:

        eligible = []

        for volume_uid in volumes:
            has_mask = False
            for label in labels:
                task_prompt = (
                    f"Check if a mask exists for:\n"
                    f"series_instance_uid: {volume_uid}\n"
                    f"label: {label}\n"
                    f"Use check_having_mask tool and respond with ONLY JSON:\n"
                    f'{{"mask_exists": true}} or {{"mask_exists": false}}\n'
                    f"No other text."
                )
                
                logger.info(f"Checking mask: volume={volume_uid}, label={label}")
                
                try:
                    r = await self.ExecutionAgent.run(task=task_prompt)
                    
                    response_content = r.messages[-1].content
                    logger.info(f"Agent response: {response_content}")
        
                    result = json.loads(response_content)
                    has_mask = result.get("mask_exists", False)
                except Exception as e:
                    logger.error(f"Error checking mask: {e}", exc_info=True)
                    has_mask = False          
                if has_mask:
                    logger.info(f"Volume {volume_uid} has existing mask for '{label}'")
                    break

            if not has_mask:
                eligible.append(volume_uid)
                logger.info(f"Volume {volume_uid} added to eligible list")
            else:
                logger.info(f"Volume {volume_uid} excluded (has mask)")
        
        return eligible

    async def _run_automatic(
        self, volumes: List[str], labels: List[str], selected_auto_model: str
    ):
        logger.info(f"Starting automatic segmentation on {len(volumes)} volumes using model '{selected_auto_model}'")
        
        for idx, volume_uid in enumerate(volumes, start=1):
            logger.info(f"[{idx}/{len(volumes)}] Running segmentation for volume: {volume_uid}")
            try:
                result = await self.ExecutionAgent.run(
                    task=(
                        f"run the segmentation for:\n"
                        f"series_instance_uid: {volume_uid}\n"
                        f"labels: {labels}\n"
                        f"model_name: {selected_auto_model}"
                    )
                )
                logger.info(f"Completed segmentation for volume: {volume_uid}")
            except Exception as e:
                logger.error(f"Segmentation failed for volume {volume_uid}: {e}")     

    async def _run_interactive(self, labels: List[str]):
        logger.info(f"Starting interactive segmentation guidance for labels: {labels}")
        
        # console 
        logger.info("Providing initial guidance to user...")
        await Console(
            self.UserFallbackAgent.run_stream(
                task=(
                    f"Guide the user through interactive segmentation for these labels: {labels}. "
                    "Explain available interactive segmentation models and their parameters. "
                    "answer there question precisely and also provide precise overview unless they ask for details"
                    "Tell the user they can ask questions, or press Enter if they have no more questions."
                    "terminare when they confirm that they make all masks needed you need to check this."
                )
            )
        )

        logger.info("Entering interactive Q&A loop")
        
        loop = asyncio.get_running_loop()

        while True:
            user_input = await loop.run_in_executor(
                None, input, "\nUser (type your question, or press Enter to finish)> "
            )

            if not user_input.strip():
                logger.info("User pressed Enter with no input - terminating session")
                print("\nInteractive session ended.\n")
                break
            
            # termination words
            exit_phrases = ["done", "finish", "finished", "no more", "that's all", "exit", "quit", "bye", "complete"]
            if any(phrase in user_input.lower() for phrase in exit_phrases):
                logger.info(f"User indicated completion: '{user_input}'")
                print("\ Great! Interactive session completed.\n")
                break

            logger.info(f"[User Input] {user_input}")
            
            task_message = (
                f"The user asked: \"{user_input}\"\n\n"
                f"Context: We are guiding them through interactive segmentation for labels: {labels}\n\n"
                "Please provide a concise answer to their specific question. "
                "Do NOT repeat previous answers or conversation history."
            )

            try:
                print("\nUserFallbackAgent> ", end="", flush=True)
                
                full_response = ""
                async for message in self.UserFallbackAgent.run_stream(task=task_message):
                    if hasattr(message, 'content') and message.content:
                        print(message.content, end="", flush=True)
                        full_response += message.content
                
                print("\n") 
                
                logger.info(f"[Agent Response] {full_response}")
                
            except Exception as e:
                logger.error(f"Error during interactive response: {e}", exc_info=True)
                print(f"\n Error: {str(e)}\n")
                break

        logger.info("Interactive session completed.")

    async def download_and_pair_volumes(self, datasetlist, chosen_path, selected_auto_model_for_volume):
        """
        Downloads volumes and their associated labels.
        """
        VOLUMES = datasetlist
        LABELS_BY_VOLUME_URL = "http://localhost:8000/segmentations/by-volume/"
        DOWNLOAD_IMAGES_URL = "http://localhost:8000/datastore/images"
        DOWNLOAD_LABELS_URL = "http://localhost:8000/datastore/labels"
        TASK_NAME = "final"

        current_file = Path(__file__)
        current_dir = current_file.parent.parent.parent

        DOWNLOAD_DIR_IMG = os.path.join(current_dir, "apps", "radiology", "General_Data")
        DOWNLOAD_DIR_LABEL = os.path.join(current_dir, "apps", "radiology", "Labels")
        FINAL_DIR_IMG = os.path.join(current_dir, "mas", "memory", "workspace", "volumes_data")
        FINAL_DIR_LABEL = os.path.join(current_dir, "mas", "memory", "workspace", "labels_data")

        os.makedirs(DOWNLOAD_DIR_IMG, exist_ok=True)
        os.makedirs(DOWNLOAD_DIR_LABEL, exist_ok=True)
        os.makedirs(FINAL_DIR_IMG, exist_ok=True)
        os.makedirs(FINAL_DIR_LABEL, exist_ok=True)

        def fetch_labels_for_volume(volume_uid):
            """Fetch all labels associated with a volume from the API"""
            url = LABELS_BY_VOLUME_URL + volume_uid
            logger.info(f"Fetching labels from: {url}")
            try:
                r = requests.get(url, timeout=10)
                if r.status_code != 200:
                    logger.warning(f"GET {url} returned {r.status_code}: {r.text}")
                    return []
                labels = r.json()
                logger.info(f"Received {len(labels)} labels for volume {volume_uid}")
                if labels:
                    logger.debug(f"Labels data: {json.dumps(labels, indent=2)}")
                return labels
            except Exception as e:
                logger.error(f"Error fetching labels for {volume_uid}: {e}")
                return []

        def pick_latest_or_auto(labels, chosen_path="interactive", selected_auto_model=None):
            """
            Select the appropriate label based on the path:
            - automatic: Match segmentation_type with selected_auto_model
            - interactive: Pick the latest by created_at
            """
            if not labels:
                logger.warning("No labels provided to pick_latest_or_auto")
                return None
            
            logger.info(f"Picking label from {len(labels)} options (path={chosen_path}, model={selected_auto_model})")
            
            # match model for automatic path
            if chosen_path == "automatic" and selected_auto_model:
                logger.info(f"Searching for label with segmentation_type='{selected_auto_model}'")
                for label in labels:
                    seg_type = label.get("segmentation_type")
                    logger.debug(f"Checking label: segmentation_type='{seg_type}', created_at='{label.get('created_at')}'")
                    if seg_type == selected_auto_model:
                        logger.info(f" Found matching automatic label: {label.get('label_series_instance_uid')}")
                        return label
                logger.warning(f"No label found with segmentation_type='{selected_auto_model}', falling back to latest")
            
            # the latest created mask
            latest = max(labels, key=lambda x: x.get("created_at", ""))
            logger.info(f"Selected latest label: {latest.get('label_series_instance_uid')} (created_at={latest.get('created_at')})")
            return latest

        def post_trigger_download(url, task_name, uids_list):
            """Trigger download API endpoint"""
            if not uids_list:
                logger.warning(f"Empty UID list for {url}, skipping")
                return None
            
            logger.info(f"POST to {url} with {len(uids_list)} UIDs: {uids_list}")
            try:
                response = requests.post(url, params={"task_name": task_name}, json=uids_list, timeout=60)
                logger.info(f"Download trigger response: {response.status_code}")
                if response.status_code != 200:
                    logger.error(f"Download failed: {response.text}")
                return response
            except Exception as e:
                logger.error(f"ERROR posting to {url}: {e}")
                return None

        async def wait_for_files_appear(target_dir, expected_uids, timeout_s=60, poll_interval=2.0):
            """Wait for files to appear in the download directory"""
            if not expected_uids:
                logger.info("No UIDs to wait for")
                return set()
            
            deadline = time.time() + timeout_s
            found = set()
            logger.info(f"Waiting for {len(expected_uids)} files in {target_dir}")
            logger.info(f"Expected UIDs: {expected_uids}")
            
            while time.time() < deadline:
                current_files = os.listdir(target_dir)
                for name in current_files:
                    if not name.lower().endswith(".nii.gz"):
                        continue
                    for uid in expected_uids:
                        if uid in name and name not in found:
                            found.add(name)
                            logger.info(f"Found file: {name}")
                
                if len(found) >= len(expected_uids):
                    logger.info(f"All {len(expected_uids)} files found!")
                    return found
                
                remaining = time.time() - deadline
                logger.debug(f"Found {len(found)}/{len(expected_uids)} files, waiting... ({remaining:.1f}s remaining)")
                await asyncio.sleep(poll_interval)
            
            logger.warning(f"Timeout Only found {len(found)}/{len(expected_uids)} files: {found}")
            return found

        def move_matching_niigz(src_dir, dst_dir, matching_uids=None):
            """Move volume files from source to destination"""
            moved = []
            for fname in os.listdir(src_dir):
                if not fname.lower().endswith(".nii.gz"):
                    continue
                if matching_uids and not any(uid in fname for uid in matching_uids):
                    continue
                src_path = os.path.join(src_dir, fname)
                dst_path = os.path.join(dst_dir, fname)
                shutil.move(src_path, dst_path)
                moved.append(fname)
                logger.info(f"Moved volume: {fname}")
            logger.info(f"Total volumes moved: {len(moved)}")
            return moved

        def move_and_rename_labels(src_dir, dst_dir, label_uids, volume_uids):
            """
            Move label files and rename them to match their corresponding volume UIDs.
            This ensures labels have the same filename as their associated volumes.
            """
            if not label_uids or not volume_uids:
                logger.warning("Empty label or volume UID lists")
                return
            
            logger.info(f"Moving and renaming {len(label_uids)} labels to match volume UIDs")
            moved_count = 0
            
            for label_uid, volume_uid in zip(label_uids, volume_uids):
                # Find the file containing the label UID
                label_file = None
                for fname in os.listdir(src_dir):
                    if label_uid in fname and fname.lower().endswith(".nii.gz"):
                        label_file = fname
                        break
                
                if label_file:
                    src_path = os.path.join(src_dir, label_file)
                    # Rename to match volume UID exactly
                    dst_path = os.path.join(dst_dir, f"{volume_uid}.nii.gz")
                    shutil.move(src_path, dst_path)
                    moved_count += 1
                    logger.info(f" Renamed label: {label_file} -> {volume_uid}.nii.gz")
                else:
                    logger.warning(f"Label file not found for UID: {label_uid}")
            
            logger.info(f"Total labels moved and renamed: {moved_count}/{len(label_uids)}")

        # Main pairing logic
        volumes_to_download = []
        labels_to_download = []

        logger.info(f"Processing {len(VOLUMES)} volumes for label pairing...")
        
        for idx, vol_uid in enumerate(VOLUMES, 1):
            logger.info(f"[{idx}/{len(VOLUMES)}] Processing volume: {vol_uid}")
            
            # Fetch all labels for this volume
            labels = fetch_labels_for_volume(vol_uid)
            
            if not labels:
                logger.warning(f"No labels found for volume {vol_uid}, skipping")
                continue
            
            # Select the appropriate label based on path
            selected_model = selected_auto_model_for_volume if chosen_path == "automatic" else None
            latest = pick_latest_or_auto(labels, chosen_path=chosen_path, selected_auto_model=selected_model)
            
            if latest is None:
                logger.warning(f"No suitable label selected for volume {vol_uid}, skipping")
                continue
            
            # Extract UIDs from the selected label
            label_uid = latest.get("label_series_instance_uid")
            vol_uid_from_label = latest.get("volume_series_instance_uid") or vol_uid
            
            if not label_uid:
                logger.warning(f"Label UID is empty for volume {vol_uid}, skipping")
                continue
            
            logger.info(f"Paired: volume={vol_uid_from_label} label={label_uid}")
            volumes_to_download.append(vol_uid_from_label)
            labels_to_download.append(label_uid)

        logger.info("="*60)
        logger.info(f"PAIRING COMPLETE:")
        logger.info(f"Volumes to download: {len(volumes_to_download)}")
        logger.info(f"Labels to download: {len(labels_to_download)}")
        logger.info("="*60)

        if not volumes_to_download or not labels_to_download:
            logger.error(" No volumes or labels to download! Exiting.")
            return

        # Trigger downloads
        logger.info("Triggering volume download...")
        vol_response = post_trigger_download(DOWNLOAD_IMAGES_URL, TASK_NAME, volumes_to_download)
        
        logger.info("Triggering label download...")
        label_response = post_trigger_download(DOWNLOAD_LABELS_URL, TASK_NAME, labels_to_download)

        # Wait for files to appear in download directories
        logger.info("Waiting for volume files to appear...")
        found_volume_files = await wait_for_files_appear(DOWNLOAD_DIR_IMG, volumes_to_download, timeout_s=90)
        
        logger.info("Waiting for label files to appear...")
        found_label_files = await wait_for_files_appear(DOWNLOAD_DIR_LABEL, labels_to_download, timeout_s=90)

        logger.info("Moving volume files to final directory...")
        move_matching_niigz(DOWNLOAD_DIR_IMG, FINAL_DIR_IMG, volumes_to_download)
        
        logger.info("Moving and renaming label files...")
        move_and_rename_labels(DOWNLOAD_DIR_LABEL, FINAL_DIR_LABEL, labels_to_download, volumes_to_download)
        
        logger.info("="*60)
        logger.info("DOWNLOAD AND PAIRING COMPLETED SUCCESSFULLY!")


async def test_module():
    """Test function for the SegmentationModule"""
    try:
        logger.info("Starting SegmentationModule test...")
        
        # intialize
        agent = SegmentationModule()
        await agent.initialize()
        
        current_file = Path(__file__)
        config_path =os.path.join(current_file.parent.parent, "memory", "workspace", "configurations.json")
        logger.info(f"Running with config: {config_path}")
        
        result = await agent.run(config_path)
        
        logger.info("Test completed successfully!")
        logger.info(f"Result: {result}")
        
    except Exception as e:
        logger.error(f"Test failed with error: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(test_module())

__all__ = ["SegmentationModule"]
