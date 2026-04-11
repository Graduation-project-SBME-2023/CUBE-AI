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
import nibabel as nib
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
import numpy as np
import json
import logging
from dotenv import load_dotenv
import os
import requests
import httpx
from requests_toolbelt.multipart import decoder
from datetime import datetime
import re
from pathlib import Path
from typing import List, Dict, Any, Union
from typing import Annotated
from pydantic import Field
import os
import asyncio
from agent_system.models.tool_logging import ProgressPayload, TextPayload, ToolEvent
from agent_system.tool_logging import receive_tool_event

# load_dotenv()
# log_file_path = Path(__file__).parent.parent/"memory"/"workspace"/ "segmentation_module.log"
logger = logging.getLogger("SegmentationModule")
# logger.setLevel(logging.INFO)
# formatter = logging.Formatter(
#     "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
# )
# # console handler
# ch = logging.StreamHandler()
# ch.setFormatter(formatter)
# logger.addHandler(ch)
# # file handler
# fh = logging.FileHandler(log_file_path)
# fh.setFormatter(formatter)
# logger.addHandler(fh)

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
        backend_dir = current_file.parent.parent.parent.parent
        server_path = backend_dir / "mcp" / "server.py"   
        logger.info(f"Starting MCP server at: {server_path}")     
        math_server = StdioServerParams(
            command=sys.executable,
            args=[str(server_path)],
            timeout=None,
            cwd=str(backend_dir / "mcp")
        )
        try:
            tools = await mcp_server_tools(math_server)
            logger.info(f"Connected! Available tools count: {len(tools)}")
            
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            raise 
        
        self.discovery_tools = [t for t in tools if t.name == "get_model_info"]
        assert self.discovery_tools, "Missing get_model_info"       
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

    async def run(self, input_data: Union[str, Dict[str, Any]], workspace_folder=None):
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
        eligible_volumes = await self._exclude_existing_masks(
            datasetlist, target_labels
        )
        payload = TextPayload(
            message=f'Found existing masks for {len(datasetlist) - len(eligible_volumes)} volumes'
        )
        plan_log_event = ToolEvent(
            event_type='tool_started',
            source='segmentation_agent',
            content_type='text',
            content=payload
        )
        await receive_tool_event(plan_log_event)
        plan_log_event = ToolEvent(
            event_type='tool_finished',
            source='segmentation_agent',
            content_type='text',
            content=payload
        )
        await receive_tool_event(plan_log_event)


        logger.info(
            f"Eligible volumes for segmentation: {len(eligible_volumes)}"
        )

        # whether it's automatic or interactive
        if chosen_path == "automatic" and eligible_volumes:
            selected_auto_model = plan["automatic_models"][0]
            await self._run_automatic(eligible_volumes, target_labels, selected_auto_model, workspace_folder)

        elif eligible_volumes:
            await self._run_interactive(target_labels)

        payload = TextPayload(message=f'Segmentation...Done')
        segmentation_tool_event = ToolEvent(
            event_type='tool_finished',
            source='segmentation_agent',
            content_type='text',
            content=payload
        )
        await receive_tool_event(segmentation_tool_event)
            
        # finally download the volumes with its label masks 
        # selected_auto_model_for_volume= "segmentation_spleen" # hard test
        payload = TextPayload(message='Creating dataset...')
        environment_setup_event = ToolEvent(
            event_type='tool_started',
            source='segmentation_agent',
            content_type='text',
            content=payload
        )
        await receive_tool_event(environment_setup_event)
        environment_setup_event = ToolEvent(
            event_type='tool_progress',
            source='segmentation_agent',
            content_type='text',
            content=payload
        )
        await receive_tool_event(environment_setup_event)
        await asyncio.sleep(120)  
        await self.download_and_pair_volumes(
                datasetlist, 
                chosen_path=chosen_path, 
                selected_auto_model_for_volume=plan["automatic_models"][0] if plan["automatic_models"] else None,
                workspace_dir=workspace_folder
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
    
    def _matches_label(self, segmentation_type: str, label: str) -> bool:
        seg_norm = "".join(c for c in segmentation_type.lower() if c.isalpha())
        label_norm = "".join(c for c in label.lower() if c.isalpha())

        return label_norm in seg_norm

    async def _exclude_existing_masks(
        self, volumes: List[str], labels: List[str]
    ) -> List[str]:

        eligible = []

        for volume_uid in volumes:
            has_mask = False
            logger.info(f"Checking masks: volume={volume_uid}, labels={labels}")

            try:
                segmentations = await self.check_having_mask(volume_uid)

                # Empty list → no mask
                if not segmentations:
                    has_mask = False
                else:
                    # Smart label-based search
                    for seg in segmentations:
                        seg_type = seg.get("segmentation_type", "")
                        for label in labels:
                            if self._matches_label(seg_type, label):
                                has_mask = True
                                logger.info(
                                    f"Match found: volume={volume_uid}, "
                                    f"label={label}, segmentation_type={seg_type}"
                                )
                                break
                        if has_mask:
                            break

            except Exception as e:
                logger.error(f"Error checking mask: {e}", exc_info=True)
                has_mask = False

            if not has_mask:
                eligible.append(volume_uid)
                logger.info(f"Volume {volume_uid} added to eligible list")
            else:
                logger.info(f"Volume {volume_uid} excluded (matching mask exists)")

        return eligible
    async def check_having_mask(self, volume_uid: str):
        """
        Checks if a segmentation mask exists for the given volume SeriesInstanceUID (volume_uid).
        Calls the endpoint /segmentations/by-volume/{volume_uid} and returns the list of segmentations.
        """
        url = f"http://localhost:8000/segmentations/by-volume/{volume_uid}"
        response = requests.get(url)
        if response.status_code == 200:
            return response.json()
        else:
            return []
    async def _run_automatic(
        self, volumes: List[str], labels: List[str], selected_auto_model: str, workspace_folder: str
    ):

        payload = TextPayload(message=f'Starting Automatic Segmentation on {len(volumes)}...')
        tool_started = ToolEvent(
            event_type='tool_started',
            source='segmentation_agent',
            content_type='text',
            content=payload
        )
        await receive_tool_event(tool_started)
        tool_progress = ToolEvent(
            event_type='tool_progress',
            source='segmentation_agent',
            content_type='progress',
            content=payload
        )
        await receive_tool_event(tool_progress)

        logger.info(f"Starting automatic segmentation on {len(volumes)} volumes using model '{selected_auto_model}'")
        # try:
        #         result = await self.run_batch_segmentation(
        #             model=selected_auto_model,
        #             series_instance_uids=volumes,
        #             label_info=labels
        # )
        for idx, volume_uid in enumerate(volumes, start=1):
            payload = ProgressPayload(
                current=idx,
                total=len(volumes),
                label='Segmenting Volumes'
            )
            tool_progress = ToolEvent(
                event_type='tool_progress',
                source='segmentation_agent',
                content_type='progress',
                content=payload
            )
            await receive_tool_event(tool_progress)
            logger.info(f"[{idx}/{len(volumes)}] Running segmentation for volume: {volume_uid}")
            try:
                result = await self.run_segmentation(
                    model=selected_auto_model,
                    series_instance_uid=volume_uid,
                    label_info=labels,
                    workspace_folder=workspace_folder
                )
                # result = await self.ExecutionAgent.run(
                #     task=(
                #         f"run the segmentation for:\n"
                #         f"series_instance_uid: {volume_uid}\n"
                #         f"labels: {labels}\n"
                #         f"model_name: {selected_auto_model}"
                #     )
                # )
                logger.info(f"Completed segmentation for volume: {volume_uid}")
            except Exception as e:
                logger.error(f"Segmentation failed for {volume_uid} : {e}")    
    async def run_segmentation(self,
        model: Annotated[
            str,
            Field(description="MONAI Label model name to run inference with")
        ],
        series_instance_uid: Annotated[
            str,
            Field(description="DICOM SeriesInstanceUID to segment")
        ],
        label_info: Annotated[
            str,
            Field(description="Comma-separated anatomical labels to segment (e.g. 'spleen' or 'spleen,liver,kidney')")
        ],
        params: Annotated[
            Dict[str, Any] | None,
            Field(description="Optional inference parameters")
        ] = None,
        workspace_folder: Annotated[
            str,
            Field(description="Workspace folder to run inference in")
        ] = None,
    ):
        """
        Run MONAI Label segmentation using /infer/{model}, parse the multipart response, 
        save the segmentation mask, and upload to MONAI Label datastore as DICOM-SEG.
        
        Args:
            model: The segmentation model name (e.g., "segmentation_spleen", "segmentation_liver")
            series_instance_uid: The DICOM SeriesInstanceUID to segment
            label_info: REQUIRED list of label metadata 
        
        Returns:
            Dictionary containing:
            - status_code: HTTP response code
            - returned_params: Execution metadata from inference
            - temp_file_path: Path where .nii.gz mask was saved
            - saved_to_datastore: Whether upload to datastore succeeded
            - label_id: ID assigned by datastore (if successful)
        """
        MONAI_LABEL_URL = "http://localhost:8000"  
        start_time = datetime.now()
        logger.info(f"=== TOOL STARTED at {start_time} ===")
        
        clean_model = model.strip().rstrip(",")
        
        logger.info(f"=== run_segmentation DEBUG ===")
        logger.info(f"model: {model}")
        logger.info(f"series_instance_uid: {series_instance_uid}")
        logger.info(f"label_info type: {type(label_info)}")
        logger.info(f"label_info content: {label_info}")
        if label_info and len(label_info) > 0:
            logger.info(f"label_info[0] type: {type(label_info[0])}")
            logger.info(f"label_info[0] content: {label_info[0]}")
        logger.info(f"params: {params}")
        logger.info(f"==============================")

        normalized_label_info = []

        # Normalize label_info into a list of strings
        if not label_info:
            logger.error("label_info is empty!")
            return {
                "error": True,
                "stage": "validation",
                "message": "label_info cannot be empty"
            }

        if isinstance(label_info, list):
            organ_list = [str(o).strip().lower() for o in label_info if str(o).strip()]

        elif isinstance(label_info, str):
            organ_list = [o.strip().lower() for o in label_info.split(",") if o.strip()]

        else:
            return {
                "error": True,
                "stage": "validation",
                "message": f"Invalid label_info type: {type(label_info)}"
            }

        if not organ_list:
            return {
                "error": True,
                "stage": "validation",
                "message": "No valid organs found in label_info"
            }

        logger.info(f"Parsed organs from label_info: {organ_list}")

        
        default_colors = {
            "spleen": [255, 0, 0],      # Red
            "liver": [139, 69, 19],     # Brown
            "kidney": [255, 255, 0],    # Yellow
            "pancreas": [255, 165, 0],  # Orange
            "lung": [173, 216, 230],    # Light blue
            "heart": [255, 192, 203],   # Pink
        }
        
        # Fetch source volume metadata
        study_instance_uid = None
        
        timeout_config = httpx.Timeout(
            connect=30.0,
            read=300.0,
            write=30.0,
            pool=30.0
        )
        
        logger.info("Fetching source volume metadata...")
        try:
            info_url = f"{MONAI_LABEL_URL}/datastore/image/info"
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                info_response = await client.get(info_url, params={"image": series_instance_uid})
                if info_response.status_code == 200:
                    image_info = info_response.json()
                    study_instance_uid = image_info.get('StudyInstanceUID')
                    logger.info(f"Source StudyInstanceUID: {study_instance_uid}")
                else:
                    logger.warning(f"Could not fetch image info: {info_response.status_code}")
        except Exception as e:
            logger.warning(f"Error fetching source metadata: {e}")
        
        for idx, item in enumerate(organ_list, start=1):
            organ_name = item.lower()
            label_entry = {
                "name": organ_name,
                "idx": idx,
                "color": default_colors.get(organ_name, [255, 0, 0]),
                "description": f"{organ_name.capitalize()} segmentation",
                "model_name": clean_model
            }
            
            # Add reference to source series for proper DICOM-SEG linking
            if series_instance_uid:
                label_entry["ReferencedSeriesInstanceUID"] = series_instance_uid
            
            normalized_label_info.append(label_entry)
        
        logger.info(f"Normalized label_info: {normalized_label_info}")
        
        infer_url = f"{MONAI_LABEL_URL}/infer/{clean_model}"
        query_params = {"image": series_instance_uid,"output": "all"}

        inference_params = params or {}
        inference_params["label_info"] = normalized_label_info

        files = {"params": (None, json.dumps(inference_params), "application/json")}

        logger.info("Starting inference request...")
        logger.info(" This may take several minutes for large volumes...")
        
        async with httpx.AsyncClient(timeout=timeout_config) as client:  
            try:
                inference_start = datetime.now()
                response = await client.post(infer_url, params=query_params, files=files)
                inference_duration = (datetime.now() - inference_start).total_seconds()
                
                logger.info(f"Inference completed in {inference_duration:.1f}s with status: {response.status_code}")
                
            except httpx.TimeoutException as e:
                elapsed = (datetime.now() - start_time).total_seconds()
                logger.error(f"Inference timeout after {elapsed:.1f}s: {str(e)}")
                return {
                    "error": True, 
                    "stage": "inference_timeout", 
                    "message": f"Inference exceeded timeout after {elapsed:.1f}s. Try with smaller volume or different model.",
                    "elapsed_seconds": elapsed
                }
            except Exception as e:
                logger.error(f"Inference request failed: {str(e)}")
                return {"error": True, "stage": "inference_request", "message": str(e)}

        result = {
            "endpoint": infer_url,
            "query_params": query_params,
            "sent_params": inference_params,
            "status_code": response.status_code,
            "inference_duration_seconds": inference_duration
        }

        if response.status_code != 200:
            result["error"] = True
            result["server_response"] = response.text[:500] 
            logger.error(f"Inference failed with status {response.status_code}: {response.text[:200]}")
            return result

        # parse the data
        logger.info("Parsing multipart response...")
        try:
            content_type = response.headers.get('content-type', '')
            
            if 'multipart' not in content_type.lower():
                return {
                    "error": True,
                    "message": "Response is not multipart",
                    "content_type": content_type,
                    "preview": str(response.content[:500])
                }
            
            multipart = decoder.MultipartDecoder(response.content, content_type)
            
            if len(multipart.parts) < 2:
                return {
                    "error": True,
                    "message": f"Expected 2 parts, got {len(multipart.parts)}",
                    "parts_count": len(multipart.parts)
                }
            
            # json part (part 0)
            try:
                json_bytes = multipart.parts[0].content
                json_text = json_bytes.decode('utf-8').strip()
                returned_params = json.loads(json_text)
            except UnicodeDecodeError as e:
                return {
                    "error": True,
                    "message": f"Part 0 is not valid UTF-8: {str(e)}",
                    "part0_preview": str(multipart.parts[0].content[:200])
                }
            except json.JSONDecodeError as e:
                return {
                    "error": True,
                    "message": f"Part 0 is not valid JSON: {str(e)}",
                    "part0_text": json_text[:500]
                }
            
            # segmentation bytes (part 1)
            seg_bytes = multipart.parts[1].content
            logger.info(f"Parsed segmentation data: {len(seg_bytes)} bytes")
            
            disposition = multipart.parts[1].headers.get(b'Content-Disposition', b'').decode('utf-8')
            original_filename = "segmentation.nii.gz"
            if 'filename=' in disposition:
                match = re.search(r'filename="([^"]+)"', disposition)
                if match:
                    original_filename = match.group(1)

            result.update({
                "error": False,
                "returned_params": returned_params,
                "original_filename": original_filename,
                "segmentation_bytes_count": len(seg_bytes),
                "message": "Segmentation completed successfully"
            })
            
            # save to temp file
            filename = f"seg_{series_instance_uid}_{clean_model}.nii.gz"
            current_file = Path(__file__)
            current_dir = current_file.parent.parent
            if not workspace_folder:
                workspace_folder = os.path.join(current_dir, "workspace")
            temp_dir = os.path.join(workspace_folder, "temp_segmentations")
            os.makedirs(temp_dir, exist_ok=True)
            temp_file_path = os.path.join(temp_dir, filename)
            
            logger.info(f"Saving segmentation to: {temp_file_path}")
            with open(temp_file_path, 'wb') as f:
                f.write(seg_bytes)
            result["temp_file_path"] = str(temp_file_path)
            logger.info("Segmentation file saved successfully")
            
            # Validate segmentation file
            logger.info("Validating segmentation spatial information...")
            try:
                import nibabel as nib
                seg_nifti = nib.load(temp_file_path)
                logger.info(f"Segmentation shape: {seg_nifti.shape}")
                logger.info(f"Segmentation affine:\n{seg_nifti.affine}")
                logger.info(f"Segmentation voxel dims: {seg_nifti.header.get_zooms()}")
                
                # Check if segmentation has any non-zero voxels
                data = seg_nifti.get_fdata()
                nonzero_count = (data > 0).sum()
                logger.info(f"Non-zero voxels in segmentation: {nonzero_count}")
                if nonzero_count == 0:
                    logger.warning("Segmentation appears to be empty (no non-zero voxels)")
            except Exception as e:
                logger.warning(f"Could not validate segmentation file: {e}")

        except Exception as e:
            import traceback
            result.update({
                "error": True,
                "message": "Failed to parse multipart response",
                "exception": str(e),
                "exception_type": type(e).__name__,
                "traceback": traceback.format_exc()
            })
            logger.error(f"Multipart parsing failed: {traceback.format_exc()}")
            return result

        # save to MONAI Label datastore
        logger.info("Starting datastore save...")
        
        label_info_payload = {
            "label_info": normalized_label_info,
        }
        
        # Add StudyInstanceUID if we retrieved it
        if study_instance_uid:
            label_info_payload["StudyInstanceUID"] = study_instance_uid
            logger.info(f"Including StudyInstanceUID in payload: {study_instance_uid}")

        if "latencies" in returned_params:
            label_info_payload["latencies"] = returned_params["latencies"]
        
        logger.info(f"Final label_info_payload: {json.dumps(label_info_payload, indent=2)}")
        
        save_url = f"{MONAI_LABEL_URL}/datastore/label"
        save_params = {
            "image": series_instance_uid,
            "tag": "final"
        }

        # extended timeout for upload as well
        upload_timeout = httpx.Timeout(
            connect=30.0,
            read=120.0,
            write=120.0,
            pool=30.0
        )

        try:
            logger.info(f"Reading file for upload: {temp_file_path}")
            with open(temp_file_path, 'rb') as label_file:
                label_content = label_file.read()
            
            logger.info(f"File read successfully, size: {len(label_content)} bytes")
            
            save_files = {
                "params": (None, json.dumps(label_info_payload), "application/json"),
                "label": (filename, label_content, "application/octet-stream")
            }
            
            logger.info(f"Uploading to datastore: {save_url}")
            logger.info(f"Upload params: {save_params}")
            
            async with httpx.AsyncClient(timeout=upload_timeout) as client:
                upload_start = datetime.now()
                save_resp = await client.put(save_url, params=save_params, files=save_files)
                upload_duration = (datetime.now() - upload_start).total_seconds()
                
                logger.info(f"Datastore upload completed in {upload_duration:.1f}s with status: {save_resp.status_code}")
                
                if save_resp.status_code == 200:
                    result["saved"] = True
                    result["saved_filename"] = filename
                    result["label_id"] = save_resp.text.strip()
                    result["upload_duration_seconds"] = upload_duration
                    logger.info(f"Successfully saved to datastore with label_id: {result['label_id']}")
                else:
                    result["saved"] = False
                    result["save_response"] = save_resp.text[:500]
                    result["save_status_code"] = save_resp.status_code
                    logger.error(f"Datastore save failed: {save_resp.text[:200]}")

        except httpx.TimeoutException as e:
            result["saved"] = False
            result["save_error"] = f"Upload timeout: {str(e)}"
            logger.error(f"Datastore upload timeout: {str(e)}")
        except Exception as e:
            import traceback
            result["saved"] = False
            result["save_error"] = str(e)
            result["save_traceback"] = traceback.format_exc()
            logger.error(f"Exception during datastore save: {traceback.format_exc()}")

        total_duration = (datetime.now() - start_time).total_seconds()
        result["total_duration_seconds"] = total_duration
        
        logger.info(f"=== TOOL COMPLETED at {datetime.now()} ===")
        logger.info(f"Total execution time: {total_duration:.1f}s")
        
        return result
    async def run_batch_segmentation(
        self,
        model: Annotated[
            str,
            Field(description="MONAI Label model name to run inference with")
        ],
        series_instance_uids: Annotated[
            List[str],
            Field(description="List of DICOM SeriesInstanceUIDs to segment")
        ],
        label_info: Annotated[
            str,
            Field(description="Comma-separated anatomical labels to segment (e.g. 'spleen' or 'spleen,liver,kidney')")
        ],
        max_workers: Annotated[
            int,
            Field(description="Number of parallel workers (default: 1 to avoid server overload)")
        ] = 1,
        max_batch_size: Annotated[
            int,
            Field(description="Maximum batch size (default: 1)")
        ] = 1,
        params: Annotated[
            Dict[str, Any] | None,
            Field(description="Optional inference parameters")
        ] = None,
    ):
        """
        Run MONAI Label batch segmentation using /batch/infer/{model}.
        The server automatically saves results to datastore - no manual parsing needed!
        
        Args:
            model: The segmentation model name (e.g., "segmentation_spleen")
            series_instance_uids: List of DICOM SeriesInstanceUIDs to segment
            label_info: Comma-separated list of anatomical labels
            max_workers: Number of parallel workers (keep at 1 to avoid DICOMweb overload)
            max_batch_size: Maximum batch size
            params: Optional inference parameters
        
        Returns:
            Dictionary containing:
            - job_id: The batch job identifier
            - total_images: Number of images to process
            - status: Job status
        """
        MONAI_LABEL_URL = "http://localhost:8000"          
        start_time = datetime.now()
        logger.info(f"=== BATCH SEGMENTATION STARTED at {start_time} ===")
        
        clean_model = model.strip().rstrip(",")
        
        logger.info(f"Model: {clean_model}")
        logger.info(f"Series UIDs: {series_instance_uids}")
        logger.info(f"Label info: {label_info}")
        logger.info(f"Total volumes: {len(series_instance_uids)}")
        
        # Normalize label_info into a list of strings
        if not label_info:
            logger.error("label_info is empty!")
            return {
                "error": True,
                "stage": "validation",
                "message": "label_info cannot be empty"
            }

        if isinstance(label_info, list):
            organ_list = [str(o).strip().lower() for o in label_info if str(o).strip()]
        elif isinstance(label_info, str):
            organ_list = [o.strip().lower() for o in label_info.split(",") if o.strip()]
        else:
            return {
                "error": True,
                "stage": "validation",
                "message": f"Invalid label_info type: {type(label_info)}"
            }

        if not organ_list:
            return {
                "error": True,
                "stage": "validation",
                "message": "No valid organs found in label_info"
            }

        logger.info(f"Parsed organs from label_info: {organ_list}")
        
        # Build label_names dictionary (CRITICAL: MONAI Label expects this format)
        label_names = {organ_name: idx for idx, organ_name in enumerate(organ_list, start=1)}
        logger.info(f"Label names mapping: {label_names}")
        
        # Build segment attributes for proper DICOM-SEG encoding
        default_colors = {
            "spleen": [255, 0, 0],
            "liver": [139, 69, 19],
            "kidney": [255, 255, 0],
            "pancreas": [255, 165, 0],
            "lung": [173, 216, 230],
            "heart": [255, 192, 203],
        }
        
        # SNOMED CT codes for common organs
        snomed_codes = {
            "spleen": ("78961009", "Spleen"),
            "liver": ("10200004", "Liver"),
            "kidney": ("64033007", "Kidney"),
            "pancreas": ("15776009", "Pancreas"),
            "lung": ("39607008", "Lung"),
            "heart": ("80891009", "Heart"),
        }
        
        segment_attributes = []
        for idx, organ_name in enumerate(organ_list, start=1):
            code_value, code_meaning = snomed_codes.get(organ_name, ("78961009", organ_name.capitalize()))
            
            segment_attributes.append([{
                "labelID": idx,
                "SegmentLabel": organ_name,
                "SegmentDescription": f"{organ_name.capitalize()} segmentation",
                "SegmentAlgorithmType": "AUTOMATIC",
                "SegmentAlgorithmName": clean_model,
                "SegmentedPropertyCategoryCodeSequence": {
                    "CodeValue": "123037004",
                    "CodingSchemeDesignator": "SCT",
                    "CodeMeaning": "Anatomical Structure"
                },
                "SegmentedPropertyTypeCodeSequence": {
                    "CodeValue": code_value,
                    "CodingSchemeDesignator": "SCT",
                    "CodeMeaning": code_meaning
                },
                "recommendedDisplayRGBValue": default_colors.get(organ_name, [255, 0, 0])
            }])
        
        logger.info(f"Segment attributes: {json.dumps(segment_attributes, indent=2)}")
        
        # Prepare batch infer request
        batch_url = f"{MONAI_LABEL_URL}/batch/infer/{clean_model}"
        
        # Build request payload with correct parameter structure
        request_payload = {
            "images": series_instance_uids,
            "save_label": True,
            "label_tag": "final",
            "max_workers": max_workers,
            "max_batch_size": max_batch_size,
            "params": {
                # CRITICAL: Use label_names dict, not label_info list
                "label_names": label_names,
                # Add segment attributes for proper DICOM-SEG encoding
                "segment_attributes": segment_attributes,
                # Merge any additional params
                **(params or {})
            }
        }
        
        logger.info(f"Batch request payload: {json.dumps(request_payload, indent=2)}")
        
        # Extended timeout for batch job submission
        timeout_config = httpx.Timeout(
            connect=30.0,
            read=60.0,
            write=30.0,
            pool=30.0
        )
        
        try:
            logger.info(f"Submitting batch job to: {batch_url}")
            
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                response = await client.post(
                    batch_url,
                    json=request_payload,
                    headers={"Content-Type": "application/json"}
                )
            
            logger.info(f"Batch job submitted with status: {response.status_code}")
            
            if response.status_code != 200:
                return {
                    "error": True,
                    "stage": "batch_submission",
                    "status_code": response.status_code,
                    "message": response.text[:500]
                }
            
            # Parse response
            result = response.json()
            
            logger.info(f"Batch job created successfully!")
            logger.info(f"Job details: {json.dumps(result, indent=2)}")
            
            total_duration = (datetime.now() - start_time).total_seconds()
            
            return {
                "error": False,
                "job_submitted": True,
                "job_info": result,
                "total_images": len(series_instance_uids),
                "organs_segmented": organ_list,
                "label_mapping": label_names,
                "submission_duration_seconds": total_duration,
                "message": (
                    f"Batch job submitted successfully! "
                    f"Processing {len(series_instance_uids)} volumes for organs: {', '.join(organ_list)}. "
                    f"Results will be automatically saved to datastore with proper DICOM-SEG labels."
                )
            }
            
        except httpx.TimeoutException as e:
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.error(f"Batch submission timeout after {elapsed:.1f}s: {str(e)}")
            return {
                "error": True,
                "stage": "batch_submission_timeout",
                "message": f"Batch job submission timed out after {elapsed:.1f}s",
                "elapsed_seconds": elapsed
            }
        except Exception as e:
            import traceback
            logger.error(f"Batch submission failed: {traceback.format_exc()}")
            return {
                "error": True,
                "stage": "batch_submission_error",
                "message": str(e),
                "traceback": traceback.format_exc()
            }
        finally:
            logger.info(f"=== BATCH SEGMENTATION COMPLETED at {datetime.now()} ===")  
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

    async def download_and_pair_volumes(self, datasetlist, chosen_path, selected_auto_model_for_volume, workspace_dir):
        """
        Downloads volumes and their associated labels.
        """
        VOLUMES = datasetlist
        LABELS_BY_VOLUME_URL = "http://localhost:8000/segmentations/by-volume/"
        DOWNLOAD_IMAGES_URL = "http://localhost:8000/datastore/images"
        DOWNLOAD_LABELS_URL = "http://localhost:8000/datastore/labels"
        TASK_NAME = "final"

        current_file = Path(__file__)
        current_dir = current_file.parent.parent.parent.parent

        DOWNLOAD_DIR_IMG = os.path.join(current_dir, "apps", "radiology", "General_Data")
        DOWNLOAD_DIR_LABEL = os.path.join(current_dir, "apps", "radiology", "Labels")
        if not workspace_dir:
            workspace_dir = os.path.join(current_dir, "agent_system", "workspace")
        FINAL_DIR_IMG = os.path.join(workspace_dir, "volumes")
        FINAL_DIR_LABEL = os.path.join(workspace_dir, "masks")

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
            Move label files, convert {0,255} -> {0,1}, and rename them
            to match their corresponding volume UIDs.
            """
            if not label_uids or not volume_uids:
                logger.warning("Empty label or volume UID lists")
                return

            logger.info(f"Moving, renaming, and normalizing {len(label_uids)} labels")
            moved_count = 0

            for label_uid, volume_uid in zip(label_uids, volume_uids):
                label_file = None
                for fname in os.listdir(src_dir):
                    if label_uid in fname and fname.lower().endswith(".nii.gz"):
                        label_file = fname
                        break

                if not label_file:
                    logger.warning(f"Label file not found for UID: {label_uid}")
                    continue

                src_path = os.path.join(src_dir, label_file)
                dst_path = os.path.join(dst_dir, f"{volume_uid}.nii.gz")

                try:
                    nii = nib.load(src_path)
                    data = nii.get_fdata()

                    unique_vals = np.unique(data)
                    logger.info(f"Label {label_file} unique values before: {unique_vals}")

                    # convert {0,255} → {0,1}
                    if np.max(data) == 255:
                        data = (data > 0).astype(np.uint8)
                        logger.info(f"Converted label {label_file} from {{0,255}} to {{0,1}}")
                    # the new updated one    
                    new_nii = nib.Nifti1Image(data, affine=nii.affine, header=nii.header)
                    nib.save(new_nii, dst_path)

                    os.remove(src_path)

                    moved_count += 1
                    logger.info(f" Renamed + normalized label → {volume_uid}.nii.gz")

                except Exception as e:
                    logger.error(f"Error processing label {label_file}: {e}")

            logger.info(f"Total labels moved and normalized: {moved_count}/{len(label_uids)}")

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
