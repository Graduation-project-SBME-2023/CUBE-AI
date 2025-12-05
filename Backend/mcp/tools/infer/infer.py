import json
import httpx
import os
from pathlib import Path
from typing import Dict, Optional
from .. import mcp


@mcp.tool()
async def monailabel_infer(
    model: str,
    base_url: str = "http://localhost:8000",
    image: str = "",
    file_path: Optional[str] = None,
    session_id: str = "",
    params: Optional[Dict] = None,
    label_path: Optional[str] = None,
    output: str = "json",
    api_token: Optional[str] = None,
    ctx: Dict | None = None,
) -> str:
    """
    Run inference using MONAI Label API for medical image segmentation.

    This tool calls a MONAI Label server to perform inference on medical images
    using trained models. It supports multiple input methods and output formats.

    Args:
        model: Name of the MONAI Label model to use for inference (required).
        base_url: URL of the MONAI Label server (default: http://localhost:8000).
        image: Image ID from the datastore (use if image already exists on server).
        file_path: Local file path to upload for inference (e.g., .nii.gz file).
        session_id: Session ID to use for inference (for session-based workflows).
        params: Additional parameters for inference as dictionary (e.g., {"label_info": {...}}).
        label_path: Local file path to label/annotation file (e.g., for scribbles).
        output: Output format - "image", "json", "all", or "dicom_seg" (default: json).
        api_token: Authentication token if the server requires authorization.
        ctx: Optional MCP context (provided automatically when invoked as a tool).

    Returns:
        A JSON string containing the inference results or error information.

    Note:
        - Must provide at least one of: image, file_path, or session_id
        - For DICOM SEG output, params must include "label_info"
        - Binary files (e.g., .bin scribbles) are automatically converted using reference image
    """
    
    return await _monailabel_infer_impl(
        model,
        base_url,
        image,
        file_path,
        session_id,
        params,
        label_path,
        output,
        api_token,
        ctx,
    )


# --------------------------
# INTERNAL IMPLEMENTATION
# --------------------------

async def _monailabel_infer_impl(
    model: str,
    base_url: str,
    image: str,
    file_path: Optional[str],
    session_id: str,
    params: Optional[Dict],
    label_path: Optional[str],
    output: str,
    api_token: Optional[str],
    ctx: Dict | None,
) -> str:
    """Internal implementation of MONAI Label inference tool."""
    
    # Validate inputs
    if not any([image, file_path, session_id]):
        error_result = {
            "success": False,
            "error": "Must provide either 'image', 'file_path', or 'session_id'"
        }
        return json.dumps(error_result, indent=2)
    
    print(f"Running MONAI Label inference with model: {model}")
    
    # Build the API URL
    url = f"{base_url}/infer/{model}"
    
    # Add output parameter to URL if specified
    if output:
        url += f"?output={output}"
    
    # Prepare form data
    data = {
        "image": image,
        "session_id": session_id,
        "params": json.dumps(params or {})
    }
    
    # Prepare files for upload
    files = {}
    opened_files = []
    
    try:
        # Handle image file upload
        if file_path:
            file_path_obj = Path(file_path)
            if not file_path_obj.exists():
                error_result = {
                    "success": False,
                    "error": f"File not found: {file_path}"
                }
                return json.dumps(error_result, indent=2)
            
            file_handle = open(file_path, "rb")
            opened_files.append(file_handle)
            files["file"] = (file_path_obj.name, file_handle)
            print(f"Uploading file: {file_path}")
        
        # Handle label file upload
        if label_path:
            label_path_obj = Path(label_path)
            if not label_path_obj.exists():
                error_result = {
                    "success": False,
                    "error": f"Label file not found: {label_path}"
                }
                return json.dumps(error_result, indent=2)
            
            label_handle = open(label_path, "rb")
            opened_files.append(label_handle)
            files["label"] = (label_path_obj.name, label_handle)
            print(f"Uploading label: {label_path}")
        
        # Prepare headers
        headers = {}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        
        # Make the API request
        print(f"Calling API: {url}")
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                url,
                data=data,
                files=files if files else None,
                headers=headers
            )
        
        # Handle response based on status and content type
        if response.status_code == 200:
            content_type = response.headers.get("content-type", "")
            
            if "application/json" in content_type:
                result = {
                    "success": True,
                    "model": model,
                    "output_format": output,
                    "content_type": "application/json",
                    "data": response.json()
                }
                print("Inference completed successfully (JSON response)")
                return json.dumps(result, indent=2)
            
            elif "multipart/form-data" in content_type:
                # For multipart responses, save the content
                result = {
                    "success": True,
                    "model": model,
                    "output_format": output,
                    "content_type": "multipart/form-data",
                    "message": "Multipart response received (image + params)",
                    "size_bytes": len(response.content)
                }
                print("Inference completed successfully (Multipart response)")
                return json.dumps(result, indent=2)
            
            elif "application/octet-stream" in content_type:
                result = {
                    "success": True,
                    "model": model,
                    "output_format": output,
                    "content_type": "application/octet-stream",
                    "message": "Binary image file received",
                    "size_bytes": len(response.content)
                }
                print(f"Inference completed successfully (Binary response: {len(response.content)} bytes)")
                return json.dumps(result, indent=2)
            
            elif "application/dicom" in content_type:
                result = {
                    "success": True,
                    "model": model,
                    "output_format": output,
                    "content_type": "application/dicom",
                    "message": "DICOM SEG file received",
                    "size_bytes": len(response.content)
                }
                print(f"Inference completed successfully (DICOM SEG: {len(response.content)} bytes)")
                return json.dumps(result, indent=2)
            
            else:
                result = {
                    "success": True,
                    "model": model,
                    "output_format": output,
                    "content_type": content_type,
                    "size_bytes": len(response.content)
                }
                return json.dumps(result, indent=2)
        
        else:
            error_result = {
                "success": False,
                "error": f"API returned status {response.status_code}",
                "details": response.text
            }
            print(f"Error: {error_result['error']}")
            return json.dumps(error_result, indent=2)
    
    except Exception as e:
        error_result = {
            "success": False,
            "error": f"Exception during inference: {str(e)}"
        }
        print(f"Error: {error_result['error']}")
        return json.dumps(error_result, indent=2)
    
    finally:
        # Close any opened files
        for file_handle in opened_files:
            file_handle.close()
