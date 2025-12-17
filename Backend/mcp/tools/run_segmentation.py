import httpx
from requests_toolbelt.multipart import decoder
import json
from datetime import datetime
from . import mcp
import re
from pathlib import Path
import logging
from typing import List, Dict, Any, Union
from typing import Annotated
from pydantic import Field
import os
import asyncio

logger = logging.getLogger(__name__)

MONAI_LABEL_URL = "http://localhost:8000"  

from typing import List, Dict, Any, Union

@mcp.tool()
async def run_segmentation(
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

    if not label_info or label_info.strip() == "":
        logger.error("label_info is empty!")
        return {"error": True, "stage": "validation", "message": "label_info cannot be empty"}
    
    organ_list = [organ.strip().lower() for organ in label_info.split(',') if organ.strip()]
    
    if not organ_list:
        return {"error": True, "stage": "validation", "message": "No valid organs found in label_info"}
    
    logger.info(f"Parsed organs from label_info: {organ_list}")
    
    default_colors = {
        "spleen": [255, 0, 0],      # Red
        "liver": [139, 69, 19],     # Brown
        "kidney": [255, 255, 0],    # Yellow
        "pancreas": [255, 165, 0],  # Orange
        "lung": [173, 216, 230],    # Light blue
        "heart": [255, 192, 203],   # Pink
    }
    
    for idx, item in enumerate(organ_list, start=1):
        organ_name = item.lower()
        normalized_label_info.append({
            "name": organ_name,
            "idx": idx,
            "color": default_colors.get(organ_name, [255, 0, 0]),  # default to red
            "description": f"{organ_name.capitalize()} segmentation",
            "model_name": clean_model
        })
    
    logger.info(f"Normalized label_info: {normalized_label_info}")
    
    infer_url = f"{MONAI_LABEL_URL}/infer/{clean_model}"
    query_params = {"image": series_instance_uid}

    inference_params = params or {}
    inference_params["label_info"] = normalized_label_info

    files = {"params": (None, json.dumps(inference_params), "application/json")}

    logger.info("Starting inference request...")
    logger.info(" This may take several minutes for large volumes...")
    
    # long timeouts for large volume inference
    timeout_config = httpx.Timeout(
        connect=30.0,      # 30 seconds to establish connection
        read=300.0,        # 5 minutes to read response
        write=30.0,        # 30 seconds to write request
        pool=30.0          # 30 seconds to get connection from pool
    )
    
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
        original_filename = "segmentation.nii.gz"  # default
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
        current_dir = current_file.parent.parent.parent
        temp_dir = os.path.join(current_dir, "mas", "memory", "workspace", "temp_segmentations")
        os.makedirs(temp_dir, exist_ok=True)
        temp_file_path = os.path.join(temp_dir, filename)
        
        logger.info(f"Saving segmentation to: {temp_file_path}")
        with open(temp_file_path, 'wb') as f:
            f.write(seg_bytes)
        result["temp_file_path"] = str(temp_file_path)
        logger.info("Segmentation file saved successfully")

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

    if "latencies" in returned_params:
        label_info_payload["latencies"] = returned_params["latencies"]
    
    save_url = f"{MONAI_LABEL_URL}/datastore/label"
    save_params = {
        "image": series_instance_uid,
        "tag": "final"  # needed for nifti conversion
    }

    # extended timeout for upload as well
    upload_timeout = httpx.Timeout(
        connect=30.0,
        read=120.0,    # 2 minutes for upload
        write=120.0,   # 2 minutes to write
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
