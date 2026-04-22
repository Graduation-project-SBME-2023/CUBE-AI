import json
import httpx
from pathlib import Path
from typing import Dict, Optional, Literal
from .. import mcp
import os


# =============================================================================
# INFERENCE ENDPOINT
# =============================================================================

@mcp.tool()
async def monailabel_infer(
    model: str,
    base_url: str = os.environ.get("MONAI_URL", "http://localhost:8000"),
    image: str = "",
    file_path: Optional[str] = None,
    session_id: str = "",
    params: Optional[Dict] = None,
    label_path: Optional[str] = None,
    output: Literal["image", "json", "all", "dicom_seg"] = "json",
    api_token: Optional[str] = None,
    ctx: Dict | None = None,
) -> str:
    """
    Run inference using MONAI Label API for medical image segmentation.

    Args:
        model: Name of the MONAI Label model to use for inference.
        base_url: URL of the MONAI Label server.
        image: Image ID from the datastore.
        file_path: Local file path to upload for inference.
        session_id: Session ID to use for inference.
        params: Additional inference parameters.
        label_path: Local file path to label/annotation file.
        output: Output format (image/json/all/dicom_seg).
        api_token: Authentication token if required.
        ctx: MCP context (auto-provided).

    Returns:
        JSON string with inference results.
    """
    return await _api_call(
        method="POST",
        endpoint=f"/infer/{model}",
        base_url=base_url,
        data={"image": image, "session_id": session_id, "params": json.dumps(params or {})},
        files=_prepare_files(file_path, label_path),
        params={"output": output} if output else None,
        api_token=api_token,
    )


# =============================================================================
# DATASTORE ENDPOINTS
# =============================================================================

@mcp.tool()
async def monailabel_get_datastore(
    base_url: str = os.environ.get("MONAI_URL", "http://localhost:8000"),
    output: Literal["train", "stats", "all"] = "stats",
    api_token: Optional[str] = None,
    ctx: Dict | None = None,
) -> str:
    """
    Get all images/labels from MONAI Label datastore.

    Args:
        base_url: URL of the MONAI Label server.
        output: Output type - 'train' (datalist), 'stats' (status), or 'all' (full json).
        api_token: Authentication token if required.
        ctx: MCP context (auto-provided).

    Returns:
        JSON string with datastore information.
    """
    return await _api_call(
        method="GET",
        endpoint="/datastore/",
        base_url=base_url,
        params={"output": output},
        api_token=api_token,
    )


@mcp.tool()
async def monailabel_add_image(
    file_path: str,
    base_url: str = os.environ.get("MONAI_URL", "http://localhost:8000"),
    image_id: Optional[str] = None,
    params: Optional[Dict] = None,
    api_token: Optional[str] = None,
    ctx: Dict | None = None,
) -> str:
    """
    Upload a new image to MONAI Label datastore.

    Args:
        file_path: Local path to the image file to upload.
        base_url: URL of the MONAI Label server.
        image_id: Optional image ID (defaults to filename).
        params: Additional parameters for the image.
        api_token: Authentication token if required.
        ctx: MCP context (auto-provided).

    Returns:
        JSON string with the created image ID.
    """
    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        return json.dumps({"success": False, "error": f"File not found: {file_path}"})
    
    return await _api_call(
        method="PUT",
        endpoint="/datastore/image",
        base_url=base_url,
        data={"image": image_id or "", "params": json.dumps(params or {})},
        files={"file": (file_path_obj.name, open(file_path, "rb"))},
        api_token=api_token,
    )


@mcp.tool()
async def monailabel_remove_image(
    image_id: str,
    base_url: str = os.environ.get("MONAI_URL", "http://localhost:8000"),
    api_token: Optional[str] = None,
    ctx: Dict | None = None,
) -> str:
    """
    Remove an image and its corresponding labels from datastore.

    Args:
        image_id: ID of the image to remove.
        base_url: URL of the MONAI Label server.
        api_token: Authentication token if required.
        ctx: MCP context (auto-provided).

    Returns:
        JSON string confirming deletion.
    """
    return await _api_call(
        method="DELETE",
        endpoint="/datastore/image",
        base_url=base_url,
        params={"id": image_id},
        api_token=api_token,
    )


@mcp.tool()
async def monailabel_download_image(
    image_id: str,
    base_url: str = os.environ.get("MONAI_URL", "http://localhost:8000"),
    api_token: Optional[str] = None,
    ctx: Dict | None = None,
) -> str:
    """
    Download an image from MONAI Label datastore.

    Args:
        image_id: ID of the image to download.
        base_url: URL of the MONAI Label server.
        api_token: Authentication token if required.
        ctx: MCP context (auto-provided).

    Returns:
        JSON string with download information.
    """
    return await _api_call(
        method="GET",
        endpoint="/datastore/image",
        base_url=base_url,
        params={"image": image_id},
        api_token=api_token,
        return_binary=True,
    )


@mcp.tool()
async def monailabel_get_image_info(
    image_id: str,
    base_url: str = os.environ.get("MONAI_URL", "http://localhost:8000"),
    api_token: Optional[str] = None,
    ctx: Dict | None = None,
) -> str:
    """
    Get metadata information for an image.

    Args:
        image_id: ID of the image.
        base_url: URL of the MONAI Label server.
        api_token: Authentication token if required.
        ctx: MCP context (auto-provided).

    Returns:
        JSON string with image metadata.
    """
    return await _api_call(
        method="GET",
        endpoint="/datastore/image/info",
        base_url=base_url,
        params={"image": image_id},
        api_token=api_token,
    )


@mcp.tool()
async def monailabel_update_image_info(
    image_id: str,
    info: Dict,
    base_url: str = os.environ.get("MONAI_URL", "http://localhost:8000"),
    api_token: Optional[str] = None,
    ctx: Dict | None = None,
) -> str:
    """
    Update metadata information for an image.

    Args:
        image_id: ID of the image.
        info: Dictionary of metadata to update.
        base_url: URL of the MONAI Label server.
        api_token: Authentication token if required.
        ctx: MCP context (auto-provided).

    Returns:
        JSON string with updated image info.
    """
    return await _api_call(
        method="PUT",
        endpoint="/datastore/image/info",
        base_url=base_url,
        params={"image": image_id},
        data={"info": json.dumps(info)},
        api_token=api_token,
    )


@mcp.tool()
async def monailabel_save_label(
    image_id: str,
    label_path: str,
    base_url: str = os.environ.get("MONAI_URL", "http://localhost:8000"),
    tag: str = "final",
    params: Optional[Dict] = None,
    api_token: Optional[str] = None,
    ctx: Dict | None = None,
) -> str:
    """
    Save a finished label/annotation to the datastore.

    Args:
        image_id: ID of the image this label belongs to.
        label_path: Local path to the label file.
        base_url: URL of the MONAI Label server.
        tag: Label tag (default: 'final').
        params: Additional parameters for the label.
        api_token: Authentication token if required.
        ctx: MCP context (auto-provided).

    Returns:
        JSON string with saved label information.
    """
    label_path_obj = Path(label_path)
    if not label_path_obj.exists():
        return json.dumps({"success": False, "error": f"Label file not found: {label_path}"})
    
    return await _api_call(
        method="PUT",
        endpoint="/datastore/label",
        base_url=base_url,
        data={"image": image_id, "tag": tag, "params": json.dumps(params or {})},
        files={"label": (label_path_obj.name, open(label_path, "rb"))},
        api_token=api_token,
    )


@mcp.tool()
async def monailabel_remove_label(
    label_id: str,
    tag: str,
    base_url: str = os.environ.get("MONAI_URL", "http://localhost:8000"),
    api_token: Optional[str] = None,
    ctx: Dict | None = None,
) -> str:
    """
    Remove a label from the datastore.

    Args:
        label_id: ID of the label to remove.
        tag: Tag of the label to remove.
        base_url: URL of the MONAI Label server.
        api_token: Authentication token if required.
        ctx: MCP context (auto-provided).

    Returns:
        JSON string confirming deletion.
    """
    return await _api_call(
        method="DELETE",
        endpoint="/datastore/label",
        base_url=base_url,
        params={"id": label_id, "tag": tag},
        api_token=api_token,
    )


@mcp.tool()
async def monailabel_download_label(
    label_id: str,
    tag: str,
    base_url: str = os.environ.get("MONAI_URL", "http://localhost:8000"),
    api_token: Optional[str] = None,
    ctx: Dict | None = None,
) -> str:
    """
    Download a label from MONAI Label datastore.

    Args:
        label_id: ID of the label to download.
        tag: Tag of the label.
        base_url: URL of the MONAI Label server.
        api_token: Authentication token if required.
        ctx: MCP context (auto-provided).

    Returns:
        JSON string with download information.
    """
    return await _api_call(
        method="GET",
        endpoint="/datastore/label",
        base_url=base_url,
        params={"label": label_id, "tag": tag},
        api_token=api_token,
        return_binary=True,
    )


@mcp.tool()
async def monailabel_get_label_info(
    label_id: str,
    tag: str,
    base_url: str = os.environ.get("MONAI_URL", "http://localhost:8000"),
    api_token: Optional[str] = None,
    ctx: Dict | None = None,
) -> str:
    """
    Get metadata information for a label.

    Args:
        label_id: ID of the label.
        tag: Tag of the label.
        base_url: URL of the MONAI Label server.
        api_token: Authentication token if required.
        ctx: MCP context (auto-provided).

    Returns:
        JSON string with label metadata.
    """
    return await _api_call(
        method="GET",
        endpoint="/datastore/label/info",
        base_url=base_url,
        params={"label": label_id, "tag": tag},
        api_token=api_token,
    )


@mcp.tool()
async def monailabel_update_label_info(
    label_id: str,
    tag: str,
    info: Dict,
    base_url: str = os.environ.get("MONAI_URL", "http://localhost:8000"),
    api_token: Optional[str] = None,
    ctx: Dict | None = None,
) -> str:
    """
    Update metadata information for a label.

    Args:
        label_id: ID of the label.
        tag: Tag of the label.
        info: Dictionary of metadata to update.
        base_url: URL of the MONAI Label server.
        api_token: Authentication token if required.
        ctx: MCP context (auto-provided).

    Returns:
        JSON string with updated label info.
    """
    return await _api_call(
        method="PUT",
        endpoint="/datastore/label/info",
        base_url=base_url,
        params={"label": label_id, "tag": tag},
        data={"info": json.dumps(info)},
        api_token=api_token,
    )


@mcp.tool()
async def monailabel_download_dataset(
    base_url: str = os.environ.get("MONAI_URL", "http://localhost:8000"),
    limit_cases: Optional[int] = None,
    api_token: Optional[str] = None,
    ctx: Dict | None = None,
) -> str:
    """
    Download full dataset as ZIP archive from MONAI Label.

    Args:
        base_url: URL of the MONAI Label server.
        limit_cases: Optional limit on number of cases to include.
        api_token: Authentication token if required.
        ctx: MCP context (auto-provided).

    Returns:
        JSON string with download information.
    """
    params = {}
    if limit_cases is not None:
        params["limit_cases"] = limit_cases
    
    return await _api_call(
        method="GET",
        endpoint="/datastore/dataset",
        base_url=base_url,
        params=params if params else None,
        api_token=api_token,
        return_binary=True,
    )


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _prepare_files(file_path: Optional[str], label_path: Optional[str]) -> Dict:
    """Prepare files dictionary for multipart upload."""
    files = {}
    if file_path:
        path_obj = Path(file_path)
        if path_obj.exists():
            files["file"] = (path_obj.name, open(file_path, "rb"))
    if label_path:
        path_obj = Path(label_path)
        if path_obj.exists():
            files["label"] = (path_obj.name, open(label_path, "rb"))
    return files if files else None


async def _api_call(
    method: str,
    endpoint: str,
    base_url: str,
    params: Optional[Dict] = None,
    data: Optional[Dict] = None,
    files: Optional[Dict] = None,
    api_token: Optional[str] = None,
    return_binary: bool = False,
) -> str:
    """
    Internal helper to make API calls to MONAI Label server.
    
    Args:
        method: HTTP method (GET, POST, PUT, DELETE).
        endpoint: API endpoint path.
        base_url: Base URL of the server.
        params: URL query parameters.
        data: Form data.
        files: Files to upload.
        api_token: Authentication token.
        return_binary: Whether response is binary data.
    
    Returns:
        JSON string with response or error.
    """
    url = f"{base_url}{endpoint}"
    headers = {}
    
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    
    opened_files = []
    try:
        print(f"Calling {method} {url}")
        
        async with httpx.AsyncClient(timeout=300.0) as client:
            if method == "GET":
                response = await client.get(url, params=params, headers=headers)
            elif method == "POST":
                response = await client.post(url, params=params, data=data, files=files, headers=headers)
            elif method == "PUT":
                response = await client.put(url, params=params, data=data, files=files, headers=headers)
            elif method == "DELETE":
                response = await client.delete(url, params=params, headers=headers)
            elif method == "HEAD":
                response = await client.head(url, params=params, headers=headers)
            else:
                return json.dumps({"success": False, "error": f"Unsupported method: {method}"})
        
        if response.status_code == 200:
            content_type = response.headers.get("content-type", "")
            
            if return_binary or "application/octet-stream" in content_type or "application/zip" in content_type:
                result = {
                    "success": True,
                    "content_type": content_type,
                    "size_bytes": len(response.content),
                    "message": "Binary data received"
                }
            elif "application/json" in content_type:
                result = {"success": True, "data": response.json()}
            else:
                result = {
                    "success": True,
                    "content_type": content_type,
                    "message": "Request completed successfully"
                }
            
            print(f"Request successful: {method} {endpoint}")
            return json.dumps(result, indent=2)
        
        else:
            error_result = {
                "success": False,
                "status_code": response.status_code,
                "error": response.text
            }
            print(f"Error {response.status_code}: {method} {endpoint}")
            return json.dumps(error_result, indent=2)
    
    except Exception as e:
        error_result = {"success": False, "error": f"Exception: {str(e)}"}
        print(f"Exception during {method} {endpoint}: {str(e)}")
        return json.dumps(error_result, indent=2)
    
    finally:
        # Close opened files
        if files:
            for file_tuple in files.values():
                if hasattr(file_tuple[1], 'close'):
                    file_tuple[1].close()