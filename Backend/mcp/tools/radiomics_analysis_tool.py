from . import mcp
from typing import Optional, Dict, Any
import os
import json
import nibabel as nib
import numpy as np
from tools.radiomics_analysis import extract_radiomics_features  # import your implementation


@mcp.tool()
async def radiomics_analysis_tool(
    segmented_volume: str,
    image: str,
    mask: Optional[str] = None,
    modality: Optional[str] = None,
    output_format: str = "json",
    ctx: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Perform radiomics-based characterization of a segmented tumor.
    Extracts high-dimensional features describing intensity, texture, and shape.

    Args:
        segmented_volume: The segmented tumor volume (path or array reference).
        image: The original medical image (path or reference).
        mask: Optional binary mask for refining the tumor region.
        modality: Imaging modality (CT, MRI, PET, etc.), used for parameter tuning.
        output_format: Output format - 'json' (default), 'markdown', or 'html'.
        ctx: Optional MCP context for logging or progress tracking.

    Returns:
        A dictionary or formatted report containing extracted radiomics features.
    """
    try:
        return await _radiomics_analysis_impl(segmented_volume, image, mask, modality, output_format, ctx)
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ======================
# Internal Implementation
# ======================
async def _radiomics_analysis_impl(segmented_volume, image, mask, modality, output_format, ctx):
    # -------------------------
    # Resolve paths
    # -------------------------
    image_path = os.path.abspath(image)
    mask_path = os.path.abspath(segmented_volume if mask is None else mask)

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")
    if not os.path.exists(mask_path):
        raise FileNotFoundError(f"Mask/segmented volume not found: {mask_path}")

    if ctx:
        ctx["progress"] = "Loading and computing features..."

    # -------------------------
    # Extract all radiomics features
    # -------------------------
    features = extract_radiomics_features(image_path, mask_path)
    features["modality"] = modality or "unknown"
    features["status"] = "Radiomics extraction completed successfully"

    # -------------------------
    # Optional: format outputs
    # -------------------------
    if output_format == "json":
        return features
    elif output_format == "markdown":
        return _format_markdown(features)
    elif output_format == "html":
        return _format_html(features)
    else:
        return features


# ======================
# Helper Functions
# ======================
def _format_markdown(results: dict) -> str:
    md = ["# Radiomics Analysis Results\n"]
    for k, v in results.items():
        md.append(f"- **{k}**: {v}")
    return "\n".join(md)


def _format_html(results: dict) -> str:
    rows = "".join([f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in results.items()])
    return f"""
    <html>
    <head><title>Radiomics Report</title></head>
    <body>
    <h2>Radiomics Analysis Results</h2>
    <table border='1' cellpadding='4' cellspacing='0'>
        {rows}
    </table>
    </body>
    </html>
    """
