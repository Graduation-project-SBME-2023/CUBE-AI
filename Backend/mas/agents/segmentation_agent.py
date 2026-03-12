"""
SegmentationModule – automates ROI segmentation.

The module receives an imaging protocol and raw volumes, then runs a
segmentation model (e.g., nnUNet, MONAI) to produce binary masks for the
structures of interest.
"""
from http import client
from pathlib import Path
from random import Random
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
import pydicom
import tempfile
import re
from typing import Dict, Optional, Tuple 
import nrrd
import numpy as np
import gzip
import io
from requests_toolbelt.multipart.decoder import MultipartDecoder 
import highdicom as hd
from pydicom.sr.codedict import codes
from pydicom.uid import generate_uid
from datetime import datetime
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
        eligible_volumes = await self._exclude_existing_masks(
            datasetlist, target_labels
        )

        logger.info(
            f"Eligible volumes for segmentation: {len(eligible_volumes)}"
        )
        # chosen_path = "automatic"
        # eligible_volumes = datasetlist
        # target_labels = ["spleen"]
        # selected_auto_model= "segmentation_spleen" # hard test
        selected_auto_model = plan["automatic_models"][0] if plan["automatic_models"] else None

        # whether it's automatic or interactive
        if chosen_path == "automatic" and eligible_volumes:
            # selected_auto_model = plan["automatic_models"][0]
            # selected_auto_model = "segmentation_spleen" # hard test
            await self._run_automatic(eligible_volumes, target_labels, selected_auto_model)

        elif eligible_volumes:
            await self._run_interactive(target_labels)
            
        # finally download the volumes with its label masks 
        # selected_auto_model_for_volume= "segmentation_spleen" # hard test
        # await asyncio.sleep(120)  
        # await self.download_and_pair_volumes(
        #         datasetlist, 
        #         chosen_path=chosen_path, 
        #         selected_auto_model_for_volume=plan["automatic_models"][0] if plan["automatic_models"] else None
        #     )

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
        self, volumes: List[str], labels: List[str], selected_auto_model: str
    ):
        logger.info(f"Starting automatic segmentation on {len(volumes)} volumes using model '{selected_auto_model}'")
        # try:
        #         result = await self.run_batch_segmentation(
        #             model=selected_auto_model,
        #             series_instance_uids=volumes,
        #             label_info=labels
        # )
        for idx, volume_uid in enumerate(volumes, start=1):
            logger.info(f"[{idx}/{len(volumes)}] Running segmentation for volume: {volume_uid}")
            try:
                result = await self.run_test_segmentation(
                    model=selected_auto_model,
                    series_instance_uid=volume_uid,
                    label_info=labels
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

    def parse_multipart_response(self,
        headers: Dict[str, str],
        body: bytes
    ) -> Tuple[bytes, dict]:
        """
        Parses a multipart/mixed response that contains:
        - One part with Content-Type: application/json     → centroids dict
        - One part with Content-Type: application/octet-stream → NRRD binary data
        Returns:
            (nrrd_bytes: bytes, centroids: dict)
        """
        content_type = headers.get('content-type', '')
        boundary_match = re.search(r'boundary=([^;]+)', content_type, re.IGNORECASE)
        if not boundary_match:
            raise ValueError("No boundary found in Content-Type header")

        boundary = boundary_match.group(1).strip()
        boundary_bytes = f"--{boundary}".encode('ascii')

        parts = self.split_multipart(body, boundary_bytes)

        json_data = None
        nrrd_bytes = None

        for part in parts:
            header_end = self.find_double_crlf(part)
            if header_end == -1:
                continue

            headers_part = part[:header_end].decode('ascii', errors='replace')
            body_part = part[header_end:]

            if 'application/json' in headers_part:
                try:
                    json_text = body_part.decode('utf-8')
                    json_data = eval(json_text)          # safer: json.loads(json_text)
                except Exception as e:
                    raise ValueError(f"Failed to parse JSON part: {e}")

            elif 'application/octet-stream' in headers_part:
                nrrd_bytes = body_part

        if json_data is None or nrrd_bytes is None:
            raise ValueError(
                "Multipart response missing required parts: "
                "need application/json and application/octet-stream"
            )

        # Return in same order as original TS → data first, then centroids
        return nrrd_bytes, json_data.get('centroids', {})

    def split_multipart(self, buffer: bytes, boundary: bytes) -> list[bytes]:
        """ 
        Splits the buffer into parts using the boundary.
        Skips the boundary line itself and trailing \r\n.
        """
        parts = []
        start = 0

        while start < len(buffer):
            boundary_index = self.find_subarray(buffer, boundary, start)
            if boundary_index == -1:
                break

            # Start of actual part content = after boundary + \r\n
            part_start = boundary_index + len(boundary) + 2
            next_boundary = self.find_subarray(buffer, boundary, part_start)

            part_end = next_boundary if next_boundary != -1 else len(buffer)

            # Exclude trailing \r\n before next boundary or end
            part = buffer[part_start : part_end - 2]
            parts.append(part)

            start = part_end

        return parts

    def find_subarray(self, haystack: bytes, needle: bytes, start: int = 0) -> int:
        """
        Returns the starting index of the first occurrence of needle in haystack[start:],
        or -1 if not found.
        """
        n_h = len(haystack)
        n_n = len(needle)

        for i in range(start, n_h - n_n + 1):
            match = True
            for j in range(n_n):
                if haystack[i + j] != needle[j]:
                    match = False
                    break
            if match:
                return i

        return -1

    def find_double_crlf(self, buffer: bytes) -> int:
        """      
        Finds the position right after the first \r\n\r\n occurrence.
        Returns -1 if not found.
        """
        for i in range(len(buffer) - 3):
            if (buffer[i]     == 13 and   # \r
                buffer[i + 1] == 10 and   # \n
                buffer[i + 2] == 13 and   # \r
                buffer[i + 3] == 10):     # \n
                return i + 4              # position after second \r\n

        return -1

    def parse_nrrd_data(self, nrrd_bytes: bytes) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Parses NRRD data (raw bytes) and returns:

        """
        try:  
            with tempfile.NamedTemporaryFile(delete=False, suffix=".nrrd") as tmp:
                tmp.write(nrrd_bytes)
                tmp_path = tmp.name


            try:
                data_array, header = nrrd.read(tmp_path)
            finally:
                os.remove(tmp_path)
          
            if data_array.dtype != np.uint16:
                print(f"Warning: NRRD data type is {data_array.dtype}, converting to uint16")
                data_array = data_array.astype(np.uint16)

            return data_array, header

        except Exception as e:
            # If pynrrd fails → fallback to manual gzip handling (rare)
            try:
                if nrrd_bytes.startswith(b'\x1f\x8b'):  # gzip magic number
                    decompressed = gzip.decompress(nrrd_bytes)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".nrrd") as tmp:
                        tmp.write(decompressed)
                        tmp_path = tmp.name
                    try:
                        data_array, header = nrrd.read(tmp_path)
                    finally:
                        os.remove(tmp_path)
                    return data_array.astype(np.uint16), header
                else:
                    raise e
            except Exception as inner_e:
                raise ValueError(f"Failed to parse NRRD data: {inner_e}") from inner_e
  
    async def fetch_source_datasets_from_orthanc(
        self,
        series_instance_uid: str,
        orthanc_url: str = "http://localhost:8043",
        timeout: float = 300.0,
    ) -> list[pydicom.Dataset]:
        """
        Fetch all DICOM instances of a series using Orthanc REST API.
        Returns list of pydicom.Dataset objects.
        """
        timeout_config = httpx.Timeout(timeout)
       
        async with httpx.AsyncClient(timeout=timeout_config) as client:
            logger.info(f"Searching series by SeriesInstanceUID: {series_instance_uid}")

            # find the internal series id
            find_payload = {
                "Level": "Series",
                "Query": {"SeriesInstanceUID": series_instance_uid}
            }
            response = await client.post(f"{orthanc_url}/tools/find", json=find_payload)
            response.raise_for_status()
            results = response.json()

            if not results:
                raise ValueError(f"Series with SeriesInstanceUID {series_instance_uid} not found in Orthanc")

            series_id = results[0] 
            logger.info(f"Resolved Orthanc series_id: {series_id}")

            # get  list of Instance IDs
            series_response = await client.get(f"{orthanc_url}/series/{series_id}")
            series_response.raise_for_status()
            series_data = series_response.json()
            instance_ids = series_data.get("Instances", [])

            if not instance_ids:
                raise ValueError(f"No instances found in series {series_instance_uid}")

            logger.info(f"Found {len(instance_ids)} instances in series {series_instance_uid}")

            # download each instance file and parse to pydicom.Dataset
            temp_dir = Path(tempfile.mkdtemp(prefix="orthanc-dicom-"))
            dicom_paths = [] 

            try:
                for i, inst_id in enumerate(instance_ids):
                    file_url = f"{orthanc_url}/instances/{inst_id}/file"
                    file_response = await client.get(file_url)
                    file_response.raise_for_status()

                    # save to tmp
                    temp_path = temp_dir / f"instance_{i:06d}.dcm"
                    with open(temp_path, "wb") as f:
                        f.write(file_response.content)

                    # Load with pydicom to get InstanceNumber for sorting
                    try:
                        ds = pydicom.dcmread(str(temp_path))
                        dicom_paths.append((ds, temp_path))
                        logger.debug(f"Saved & read {inst_id} → {temp_path}")
                    except Exception as e:
                        logger.warning(f"Failed pydicom read for {inst_id}: {e}")
                        temp_path.unlink(missing_ok=True)
                        continue

                if not dicom_paths:
                    raise ValueError("No valid DICOM files loaded")

                # sort by InstanceNumber (0020,0013), default to 0 if missing
                dicoms_sorted = sorted(
                    dicom_paths,
                    key=lambda x: int(x[0].get((0x0020, 0x0013), pydicom.DataElement((0x0020, 0x0013), 'IS', '0')).value or 0)
                )


                # source_images = []
                # for ds, path in dicoms_sorted:
                #     try:
                #         img = hd.imread(str(path))
                #         source_images.append(img)
                #     except Exception as e:
                #         logger.warning(f"highdicom.imread failed for {path}: {e}")
                #         continue

                # if not source_images:
                #     raise ValueError("Failed to load any images with highdicom")

                # logger.info(f"Loaded and sorted {len(source_images)} highdicom images")
                # return source_images
                source_images = [ds for ds, _ in dicoms_sorted]
                logger.info(f"Loaded and sorted {len(source_images)} pydicom datasets")
                return source_images

            finally:
                # Cleanup temp files
                for p in temp_dir.glob("*.dcm"):
                    p.unlink(missing_ok=True)
                temp_dir.rmdir()

    def rgb_to_dicom_cielab(self, rgb: list[float] | np.ndarray) -> list[int]:
        """
        Convert RGB [0-1] to DICOM CIELab 
        """
        import numpy as np
        
        rgb = np.asarray(rgb)  # shape (3,)
        # Simple sRGB to XYZ (D65 white point)
        xyz = np.dot(rgb, np.array([
            [0.4124, 0.3576, 0.1805],
            [0.2126, 0.7152, 0.0722],
            [0.0193, 0.1192, 0.9505]
        ]).T)
        
        xyz /= np.array([0.95047, 1.0, 1.08883])  # D65 normalize
        
        # XYZ to Lab
        xyz = np.where(xyz > 0.008856, xyz ** (1/3), (7.787 * xyz) + (16/116))
        L = (116 * xyz[1]) - 16
        a = 500 * (xyz[0] - xyz[1])
        b = 200 * (xyz[1] - xyz[2])
        
        # DICOM scales Lab to 0-255 range (approx)
        L = np.clip((L + 16) * (255 / 116), 0, 255)
        a = np.clip((a + 128) * (255 / 256), 0, 255)
        b = np.clip((b + 128) * (255 / 256), 0, 255)
        
        return [int(round(v)) for v in [L, a, b]]

    def ensure_required_patient_tags(self,datasets):
        new_for_uid = generate_uid()
        for ds in datasets:
            # ds.FrameOfReferenceUID = new_for_uid # generate it to avoid conflicts
            ds.setdefault("PatientName", "Anonymous")
            ds.setdefault("PatientID", "Unknown")
            ds.setdefault("PatientBirthDate", "")
            ds.setdefault("PatientSex", "")
            ds.setdefault("FrameOfReferenceUID", "")
            ds.setdefault("StudyInstanceUID", ds.StudyInstanceUID)
            ds.setdefault("StudyID", "")
            ds.setdefault("StudyDate", "")
            ds.setdefault("StudyTime", "")
            ds.setdefault("AccessionNumber", "")
            ds.setdefault("ReferringPhysicianName", "")
            ds.setdefault("SeriesInstanceUID", ds.SeriesInstanceUID)
            ds.setdefault("Modality", ds.get("Modality", "OT"))
                   
    async def run_test_segmentation(self,
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
    ):  
        start_time = datetime.now()
        MONAI_LABEL_URL = "http://localhost:8000"
        ORTHANC_URL = "http://localhost:8043"
        clean_model = model.strip().rstrip(",")
        
        timeout_config = httpx.Timeout(
            connect=30.0,
            read=1800.0,
            write=30.0,
            pool=30.0
        ) 
        FrameOfReferenceUID = None 
        study_instance_uid = None
        # =========== get the study from the series UID ===========
        get_study_url = f"{MONAI_LABEL_URL}/volumes/{series_instance_uid}/study"
        async with httpx.AsyncClient(timeout=timeout_config) as client:
            try:
                study_response = await client.get(get_study_url)
                if study_response.status_code == 200:
                    study_info = study_response.json()
                    study_instance_uid = study_info.get('study_uid')
                    logger.info(f"Retrieved StudyInstanceUID: {study_instance_uid} for SeriesInstanceUID: {series_instance_uid}")
                else:
                    logger.warning(f"Failed to retrieve study info for SeriesInstanceUID: {series_instance_uid}, status code: {study_response.status_code}")
            except Exception as e:
                logger.warning(f"Error retrieving study info for SeriesInstanceUID: {series_instance_uid}: {e}")
        
        # =========== try get the frameofReference ================
        # fetch frameof reference
        # try:
        #     async with httpx.AsyncClient(timeout=timeout_config) as client:
        #         logger.info(f"Searching series by SeriesInstanceUID: {series_instance_uid}")

        
        #         find_payload = {
        #             "Level": "Series",
        #             "Query": {"SeriesInstanceUID": series_instance_uid}
        #         }
        #         response = await client.post(f"{ORTHANC_URL}/tools/find", json=find_payload)
        #         response.raise_for_status()
        #         results = response.json()
        #         logger.info(f"/tools/find response: {results}")

        #         if not results:
        #             raise ValueError("Series not found")

        #         series_id = results[0]
        #         logger.info(f"Resolved Orthanc series_id: {series_id}")

        #         series_json = await client.get(f"{ORTHANC_URL}/series/{series_id}")
        #         series_json.raise_for_status()
        #         instances = series_json.json().get("Instances", [])
        #         logger.info(f"Found {len(instances)} instances in the series")

        #         if not instances:
        #             raise ValueError("No instances found in series")
        #         first_instance_id = instances[0]
        #         inst_response = await client.get(f"{ORTHANC_URL}/instances/{first_instance_id}/simplified-tags")
        #         inst_tags = inst_response.json()
        #         FrameOfReferenceUID = inst_tags.get("0020,0052")
                
        # except Exception as e:
        #     logger.exception(f"Error fetching FrameOfReferenceUID for series {series_instance_uid}")

        # ====the inference using monai label=====
                
        infer_url = f"{MONAI_LABEL_URL}/infer/{clean_model}"
        query_params = {"image": series_instance_uid,"output": "all"}
        inference_params = params or {}
        inference_params["result_extension"]=".nrrd"
        inference_params["result_dtype"] = "uint16"
        inference_params["result_compress"]= False
        inference_params["restore_label_idxs"] = False

        files = {"params": (None, json.dumps(inference_params), "application/json")}      
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
            
        # ===== parsing the response into nrrd and centroids ============
        nrrd_data, centroids = self.parse_multipart_response(
            response.headers,
            response.content
        )
        logger.info(f"Parsed multipart response: nrrd_data_size={len(nrrd_data)} bytes, centroids={list(centroids)}")
        
        # ==== parse the nrrd and extrace centroids ijk =====
        
        # parse the nrrd data into numpy array and header
        image, header = self.parse_nrrd_data(nrrd_data)
        logger.info(f"Parsed NRRD data: image_shape={image.shape}, image_dtype={image.dtype}, header_keys={list(header.keys())}")
        async with httpx.AsyncClient(timeout=timeout_config) as client:
            try:
                response_info = await client.get(f"{MONAI_LABEL_URL}/info/")
        
            except Exception as e:
                logger.warning(f"Error fetching MONAI Label info: {e}")
                
        labels = response_info.json().get("labels", [])
        logger.info(f"Available labels in MONAI Label: {labels}")
        
        # the centroid world and image coordinates 
        
        centroids_ijk = {}

        for key, value in centroids.items():
            segment_index = int(value[0])           # same as parseInt(..., 10)
            image_coords = [float(v) for v in value[1:]]   # slice(1:) → [1:]
            centroids_ijk[segment_index] = {
                "image": image_coords,
                "world": []                         # empty list, same as in JS
            }
        # ==== create the segmentation object =====
        
        segmentation_object = {
            "id": series_instance_uid,
            "label": f"Segmentation_{series_instance_uid}",
            "segments": [
                {
                    "segmentIndex": idx,
                    "label": label,
                    "isActive": True,
                    "isVisible": True
                }
                for idx, label in enumerate(labels, start=1)
            ],
            "isActive": True,
            "activeSegmentIndex": 1,
            "scalarData": image, 
            "FrameOfReferenceUID": FrameOfReferenceUID,
            "centroidsIJK": centroids_ijk
        }
        
        logger.info(f"Constructed segmentation object with {len(segmentation_object['segments'])} segments")
        # logger.info(f"Segmentation object: {segmentation_object}")
        
        # ========== create pydicom datasets for the original series ============
        dicom_datasets = await self.fetch_source_datasets_from_orthanc(
            series_instance_uid=series_instance_uid
        )
        num_slices = len(dicom_datasets)
        rows = dicom_datasets[0].Rows
        cols = dicom_datasets[0].Columns
   
      
        print("Rows, Columns, slices:", rows, cols, num_slices)


        # ========== inject missing tags due to de-identification (if any) ============
        self.ensure_required_patient_tags(dicom_datasets)
        # ============== create Algorithm description=================
        algorithm_identification = hd.AlgorithmIdentificationSequence(
                name=f'Automatic Segmentation',
                version='0.0',
                family=codes.cid7162.ArtificialIntelligence
            ) 
        # =========== create segmentation description =================
        labelmap_np: np.ndarray = segmentation_object["scalarData"]
        labelmap_np = labelmap_np.astype(np.uint8)
        labelmap_np = np.transpose(labelmap_np, (2, 1, 0)) # to be numslices, rows, cols
        print(f"Labelmap shape: {labelmap_np.shape}")
        segments = segmentation_object["segments"]
        segment_descriptions = []

        colors_rgb = [[255, 0, 0], [0, 255, 0]] * len(segments)  

        # for i, seg in enumerate(segments):
        #     idx = seg["segmentIndex"]
        #     label = seg["label"]
        #     rgb_norm = np.array(colors_rgb[i]) / 255.0
        #     cielab =self.rgb_to_dicom_cielab(rgb_norm)

        #     desc = hd.seg.SegmentDescription(
        #         segment_number=idx,
        #         segment_label=label,
        #         segmented_property_category=codes.cid7150.Tissue,
        #         segmented_property_type=codes.cid7166.ConnectiveTissue,
        #         algorithm_type=hd.seg.SegmentAlgorithmTypeValues.AUTOMATIC,
        #         algorithm_identification= algorithm_identification,
        #         # recommended_display_cielab_value=cielab
        #     )
        #     segment_descriptions.append(desc)
        description_segment = hd.seg.SegmentDescription(
            segment_number=1,
            segment_label='spleen',
            segmented_property_category=codes.cid7150.Tissue,
            segmented_property_type=codes.cid7166.ConnectiveTissue,
            algorithm_type=hd.seg.SegmentAlgorithmTypeValues.AUTOMATIC,
            algorithm_identification=algorithm_identification,
            tracking_uid=hd.UID(),
            tracking_id='Test Segmentation'
        )
        # =========== create the DICOM-SEG  ================
        print("TYPE:", type(dicom_datasets[0]))
        print("IS DATASET:", isinstance(dicom_datasets[0], pydicom.dataset.Dataset))
        now = datetime.now()
        series_time = now.strftime("%I:%M:%S %p")
        seg = hd.seg.Segmentation(
                source_images=dicom_datasets,  # references + geometry
                pixel_array=labelmap_np,        # 3D labelmap → auto 2D frames
                segmentation_type=hd.seg.SegmentationTypeValues.BINARY,
                segment_descriptions=[description_segment],
                series_instance_uid=hd.UID(),
                series_number=1001,
                sop_instance_uid=hd.UID(),
                series_description=f"{clean_model}-{series_time}-g",
                instance_number=1,
                manufacturer='Manufacturer Name',
                manufacturer_model_name='MONAI Label Backend',
                software_versions='-',
                device_serial_number='XYZ',
            )    
        logger.info(f"Created DICOM-SEG object with SeriesInstanceUID: {seg.SeriesInstanceUID}")    
        
        # ========== saving to orthanc ====================
        
        seg_bytes_io = io.BytesIO()
        seg.save_as(seg_bytes_io)
        seg_bytes_io.seek(0)
        async with httpx.AsyncClient(timeout=timeout_config) as client:
            headers = {"Content-Type": "application/dicom"}
            response = await client.post(f"{ORTHANC_URL}/instances", headers=headers, content=seg_bytes_io.read())

            if response.status_code in (200, 201):
                logger.info(f"DICOM-SEG successfully uploaded to Orthanc. InstanceID: {response.json()['ID']}")
            else:
                logger.error(f"Failed to upload DICOM-SEG: {response.status_code} {response.text}")

        
        
        return result
 
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

        # normalize label_info into a list of strings
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
        
        # fetch source volume metadata
        study_instance_uid = None
        
        timeout_config = httpx.Timeout(
            connect=30.0,
            read=1800.0,
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
            
            # add reference to source series for proper DICOM-SEG linking
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
            temp_dir = os.path.join(current_dir, "memory", "workspace", "temp_segmentations")
            os.makedirs(temp_dir, exist_ok=True)
            temp_file_path = os.path.join(temp_dir, filename)
            
            logger.info(f"Saving segmentation to: {temp_file_path}")
            with open(temp_file_path, 'wb') as f:
                f.write(seg_bytes)
            result["temp_file_path"] = str(temp_file_path)
            logger.info("Segmentation file saved successfully")
            
            #validate segmentation
            logger.info("Validating segmentation spatial information...")
            try:
                import nibabel as nib
                seg_nifti = nib.load(temp_file_path)
                logger.info(f"Segmentation shape: {seg_nifti.shape}")
                logger.info(f"Segmentation affine:\n{seg_nifti.affine}")
                logger.info(f"Segmentation voxel dims: {seg_nifti.header.get_zooms()}")
                
                # check if segmentation has any non-zero voxels
                data = seg_nifti.get_fdata()
                nonzero_count = (data > 0).sum()
                logger.info(f"Non-zero voxels in segmentation: {nonzero_count}")
                
                if nonzero_count == 0:
                    logger.error("Segmentation is empty (no non-zero voxels). Skipping save to datastore.")

                    # remove the temp file since it's useless
                    try:
                        os.remove(temp_file_path)
                        logger.info("Empty segmentation file removed.")
                    except Exception as e:
                        logger.warning(f"Could not remove temp file: {e}")

                    return {
                        "error": True,
                        "stage": "empty_segmentation",
                        "message": "Model returned empty segmentation (all voxels = 0). Nothing saved.",
                        "nonzero_voxels": int(nonzero_count),
                        "inference_duration_seconds": inference_duration
                    }

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
        
        # add StudyInstanceUID if we retrieved it
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
            write=500.0,
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
        FINAL_DIR_IMG = os.path.join(current_dir, "agent_system", "workspace", "volumes")
        FINAL_DIR_LABEL = os.path.join(current_dir, "agent_system", "workspace", "masks")

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