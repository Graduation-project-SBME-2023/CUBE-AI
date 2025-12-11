import httpx
from requests_toolbelt.multipart import decoder
import json
from datetime import datetime
from . import mcp
import re
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

MONAI_LABEL_URL = "http://localhost:8000"  

@mcp.tool()
async def run_segmentation(
    model: str, 
    series_instance_uid: str, 
    label_info: list,
    params: dict = None
):
    """
    Run MONAI Label segmentation using /infer/{model}, parse the multipart response, 
    save the segmentation mask, and upload to MONAI Label datastore as DICOM-SEG.
    
    Args:
        model: The segmentation model name (e.g., "segmentation_spleen", "segmentation_liver")
        series_instance_uid: The DICOM SeriesInstanceUID to segment
        label_info: REQUIRED list of label metadata dictionaries for DICOM-SEG conversion.
                    Each dictionary must contain:
                    {
                        "name": "organ_name",        # e.g., "spleen", "liver" (REQUIRED)
                        "idx": 1,                    # segment index (1, 2, 3...) (REQUIRED)
                        "color": [255, 0, 0],        # RGB color [R, G, B] (REQUIRED)
                        "description": "Description" # e.g., "Spleen segmentation" (REQUIRED)
                    }
                    Example: [{"name": "spleen", "idx": 1, "color": [255, 0, 0], "description": "Spleen"}]
        params: Optional additional parameters for inference (e.g., thresholds, post-processing settings)
    
    Returns:
        Dictionary containing:
        - status_code: HTTP response code
        - returned_params: Execution metadata from inference
        - temp_file_path: Path where .nii.gz mask was saved
        - saved_to_datastore: Whether upload to datastore succeeded
        - label_id: ID assigned by datastore (if successful)
    
    Example:
        # Single organ segmentation
        result = await run_segmentation(
            model="segmentation_spleen",
            series_instance_uid="1.2.826.0.1.3680043.8.498...",
            label_info=[
                {
                    "name": "spleen",
                    "idx": 1,
                    "color": [255, 0, 0],
                    "description": "Spleen segmentation"
                }
            ],
            params={}
        )
        
        # Multi-organ segmentation
        result = await run_segmentation(
            model="segmentation_organs",
            series_instance_uid="1.2.826.0.1.3680043.8.498...",
            label_info=[
                {"name": "liver", "idx": 1, "color": [255, 0, 0], "description": "Liver"},
                {"name": "spleen", "idx": 2, "color": [0, 255, 0], "description": "Spleen"}
            ],
            params={}
        )
    """
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
    
    # force label infor to suit datastore expec
    normalized_label_info = []
    
    default_colors = {
        "spleen": [255, 0, 0],      
        "liver": [139, 69, 19],   
        "kidney": [255, 255, 0],   
        "pancreas": [255, 165, 0], 
        "lung": [173, 216, 230],   
        "heart": [255, 192, 203],   
    }
    
    for idx, item in enumerate(label_info, start=1):
        if isinstance(item, str):
            organ_name = item.lower()
            normalized_label_info.append({
                "name": organ_name,
                "idx": idx,
                "color": default_colors.get(organ_name, [255, 0, 0]), # if not exist default is red
                "description": f"{organ_name.capitalize()} segmentation",
                "model_name": clean_model
            })
        elif isinstance(item, dict):
            if "model_name" not in item:
                item["model_name"] = clean_model
            normalized_label_info.append(item)
        else:
            logger.warning(f"Unknown label_info item type: {type(item)}, skipping")
    
    logger.info(f"Normalized label_info: {normalized_label_info}")
    
    infer_url = f"{MONAI_LABEL_URL}/infer/{clean_model}"
    query_params = {"image": series_instance_uid}

    # Merge label_info into params for inference
    inference_params = params or {}
    inference_params["label_info"] = normalized_label_info

    files = {"params": (None, json.dumps(params or {}), "application/json")}

    # run inference
    async with httpx.AsyncClient(timeout=None) as client:
        try:
            response = await client.post(infer_url, params=query_params, files=files)
        except Exception as e:
            return {"error": True, "stage": "inference_request", "message": str(e)}

    result = {
        "endpoint": infer_url,
        "query_params": query_params,
        "sent_params": inference_params,
        "status_code": response.status_code,
    }

    if response.status_code != 200:
        result["error"] = True
        result["server_response"] = response.text[:500]  
        return result

    # parse multpart result
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
        
        try:
            # first is the json
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
        
        #second is the bytes "seg"
        seg_bytes = multipart.parts[1].content
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
        # temp file to store on orthanc
        filename = f"seg_{series_instance_uid}_{clean_model}.nii.gz"
        current_file = Path(__file__)
        current_dir = current_file.parent
        temp_dir = current_dir.parent.parent / "temp_labels"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_file_path = temp_dir / filename
        with open(temp_file_path, 'wb') as f:
            f.write(seg_bytes)
        result["temp_file_path"] = str(temp_file_path)

    except Exception as e:
        import traceback
        result.update({
            "error": True,
            "message": "Failed to parse multipart response",
            "exception": str(e),
            "exception_type": type(e).__name__,
            "traceback": traceback.format_exc()
        })
        return result
    label_info_payload = {
        "label_info": normalized_label_info,
    }
    
    if "latencies" in returned_params:
        label_info_payload["latencies"] = returned_params["latencies"]
    
    save_url = f"{MONAI_LABEL_URL}/datastore/label"
    save_params = {
        "image": series_instance_uid,
        "tag": "final"  # important for dicom seg conversion other wise the extension should be .bin
    }

    try:
        with open(temp_file_path, 'rb') as label_file:
            save_files = {
                "params": (None, json.dumps(label_info_payload), "application/json"),
                "label": (filename, label_file, "application/octet-stream")
            }
            async with httpx.AsyncClient(timeout=None) as client:
                save_resp = await client.put(save_url, params=save_params, files=save_files)
                
                if save_resp.status_code == 200:
                    result["saved"] = True
                    result["saved_filename"] = filename
                    result["label_id"] = save_resp.text.strip()
                else:
                    result["saved"] = False
                    result["save_response"] = save_resp.text[:500]
                    result["save_status_code"] = save_resp.status_code

    except Exception as e:
        import traceback
        result["saved"] = False
        result["save_error"] = str(e)
        result["save_traceback"] = traceback.format_exc()

    return result