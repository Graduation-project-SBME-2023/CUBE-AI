import httpx
from requests_toolbelt.multipart import decoder
import json
from datetime import datetime
from . import mcp
import re

MONAI_LABEL_URL = "http://localhost:8000"  

@mcp.tool()
async def run_segmentation(model: str, series_instance_uid: str, params: dict = None):
    """
    Run MONAI Label segmentation using /infer/{model}, parse the multipart response, 
    and return the segmentation data without the binary mask in JSON.
    """
    clean_model = model.strip().rstrip(",")
    infer_url = f"{MONAI_LABEL_URL}/infer/{clean_model}"
    query_params = {"image": series_instance_uid}

    files = {"params": (None, json.dumps(params or {}), "application/json")}

    # Run the segmentation inference
    async with httpx.AsyncClient(timeout=None) as client:
        try:
            response = await client.post(infer_url, params=query_params, files=files)
        except Exception as e:
            return {"error": True, "stage": "inference_request", "message": str(e)}

    result = {
        "endpoint": infer_url,
        "query_params": query_params,
        "sent_params": params,
        "status_code": response.status_code,
    }

    if response.status_code != 200:
        result["error"] = True
        result["server_response"] = response.text[:500]  # Truncate for safety
        return result

    # Parse multipart response - MUST use bytes, not text
    try:
        # Get content type header
        content_type = response.headers.get('content-type', '')
        
        if 'multipart' not in content_type.lower():
            return {
                "error": True,
                "message": "Response is not multipart",
                "content_type": content_type,
                "preview": str(response.content[:500])
            }
        
        # Parse multipart using raw bytes
        multipart = decoder.MultipartDecoder(response.content, content_type)
        
        if len(multipart.parts) < 2:
            return {
                "error": True,
                "message": f"Expected 2 parts, got {len(multipart.parts)}",
                "parts_count": len(multipart.parts)
            }
        
        # Part 1: JSON metadata (stats, latencies, etc.)
        try:
            json_bytes = multipart.parts[0].content
            # Remove any leading/trailing whitespace or BOM
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
        
        # Part 2: Binary segmentation file (.nii.gz)
        seg_bytes = multipart.parts[1].content
        
        # Extract filename from Content-Disposition header
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
            # DO NOT include seg_bytes in result - it breaks JSON
            # Store it separately or save to disk
            "message": "Segmentation completed successfully"
        })
        
        # Optional: Save to file system temporarily
        # timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        # temp_filename = f"/tmp/seg_{series_instance_uid}_{clean_model}_{timestamp}.nii.gz"
        # with open(temp_filename, 'wb') as f:
        #     f.write(seg_bytes)
        # result["temp_file_path"] = temp_filename

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

    # return result

    # Generate unique filename with timestamp & model
    # timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    filename = original_filename or f"seg_{series_instance_uid}_{clean_model}.nii.gz"

    # Optional: Save to MONAI Label datastore
    # Uncomment the section below if you want to auto-save
    
    save_url = f"{MONAI_LABEL_URL}/datastore/label"
    save_files = {
        "params": (None, json.dumps(params or {}), "application/json"),
        "label": (filename, seg_bytes, "application/octet-stream")
    }
    save_query = {"image": series_instance_uid}

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            save_resp = await client.put(save_url, params=save_query, files=save_files)
            if save_resp.status_code == 200:
                result["saved"] = True
                result["saved_filename"] = filename
            else:
                result["saved"] = False
                result["save_response"] = save_resp.text
                result["save_status_code"] = save_resp.status_code

    except Exception as e:
        result["saved"] = False
        result["save_error"] = str(e)
    

    return result