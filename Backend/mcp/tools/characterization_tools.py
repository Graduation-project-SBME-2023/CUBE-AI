import os
import json
import nibabel as nib
import numpy as np
import matplotlib.pyplot as plt
from typing import Dict
from datetime import datetime
from . import mcp

from radiomics_analysis import extract_radiomics_features, compute_voxel_spacing_mm, compute_3d_shape_features

@mcp.tool()
async def characterize_tumor(
    segmented_volume: str | None = None,
    metadata: dict | None = None,
    output_format: str = "json",
    include_visualization: bool = True,
    include_summary: bool = True,
    ctx: Dict | None = None,
) -> str:
    """
    Characterize a segmented tumor using multiple complementary analysis techniques beyond radiomics.

    Args:
        segmented_volume: Path or identifier of the segmented 3D tumor volume.
        metadata: Optional dictionary of patient or imaging metadata.
        output_format: Format of the output report ('json', 'markdown', 'html').
        include_visualization: Whether to include visualization hooks or previews.
        include_summary: Whether to include summary statistics in the report.
        ctx: Optional MCP context (provided automatically when invoked as a tool).

    Returns:
        A structured tumor characterization report in the specified format.
    """


    return await _characterize_tumor_impl(
        segmented_volume,
        metadata,
        output_format,
        include_visualization,
        include_summary,
        ctx,
    )


# --------------------------
# INTERNAL IMPLEMENTATION
# --------------------------

async def _characterize_tumor_impl(
    segmented_volume: str | None,
    metadata: dict | None,
    output_format: str,
    include_visualization: bool,
    include_summary: bool,
    ctx: Dict | None,
) -> str:

    if segmented_volume is None or not os.path.exists(segmented_volume):
        raise FileNotFoundError("Segmented tumor volume not found or not provided.")

    print(f"Processing: {segmented_volume}")

    # --- Load data ---
    img = nib.load(segmented_volume).get_fdata()
    mask = img > 0  # assume the provided volume is already segmented

    # --- Compute features ---
    voxel_spacing = compute_voxel_spacing_mm(segmented_volume)
    shape_features = compute_3d_shape_features(mask, voxel_spacing)

    # Radiomics-style intensity features
    intensity_features = {}
    values = img[mask]
    if values.size > 0:
        hist, _ = np.histogram(values, bins=32)
        hist = hist + 1e-12
        hist = hist / hist.sum()
        intensity_features = {
            "mean": float(np.mean(values)),
            "std": float(np.std(values)),
            "min": float(np.min(values)),
            "max": float(np.max(values)),
            "median": float(np.median(values)),
            "entropy": float(-np.sum(hist * np.log2(hist))),
            "skewness": float(((values - np.mean(values)) ** 3).mean() / (np.std(values) ** 3 + 1e-9)),
            "kurtosis": float(((values - np.mean(values)) ** 4).mean() / (np.std(values) ** 4 + 1e-9)),
        }

    # --- Visualization (optional) ---
    visualization_path = None
    if include_visualization:
        mid_slice = img.shape[2] // 2
        plt.figure(figsize=(6, 6))
        plt.imshow(img[:, :, mid_slice], cmap="gray")
        plt.contour(mask[:, :, mid_slice], colors="r", linewidths=0.7)
        plt.title("Tumor Segmentation (Mid Slice)")
        visualization_path = os.path.join(
            "./results/characterization_visuals",
            f"tumor_vis_{os.path.basename(segmented_volume).replace('.nii.gz', '')}.png"
        )
        os.makedirs(os.path.dirname(visualization_path), exist_ok=True)
        plt.axis("off")
        plt.savefig(visualization_path, bbox_inches="tight", dpi=120)
        plt.close()

    # --- Build report ---
    report = {
        "file": segmented_volume,
        "timestamp": datetime.now().isoformat(),
        "metadata": metadata or {},
        "morphology": shape_features,
        "intensity": intensity_features,
        "functional": {},   # placeholder for PET/DCE/DWI metrics
        "anatomical": {},   # placeholder for distances or atlas overlap
        "temporal": {},     # placeholder for longitudinal data
        "visualization": visualization_path if include_visualization else None,
    }

    if include_summary:
        report["summary"] = {
            "tumor_volume_mm3": shape_features.get("volume_mm3", None),
            "mean_intensity": intensity_features.get("mean", None),
            "sphericity": shape_features.get("sphericity", None),
        }

    # --- Save JSON ---
    out_dir = "./results/characterization_reports"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{os.path.basename(segmented_volume).replace('.nii.gz', '')}_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"Report saved to: {out_path}")

    # --- Return formatted output ---
    if output_format == "json":
        return json.dumps(report, indent=2)
    elif output_format == "markdown":
        md = f"## Tumor Characterization Report\n\n"
        md += f"**File:** {segmented_volume}\n\n"
        md += f"**Volume:** {shape_features.get('volume_mm3', 'N/A')} mm³\n"
        md += f"**Sphericity:** {shape_features.get('sphericity', 'N/A')}\n"
        md += f"**Mean Intensity:** {intensity_features.get('mean', 'N/A')}\n"
        if visualization_path:
            md += f"\n![Tumor Visualization]({visualization_path})\n"
        return md
    elif output_format == "html":
        html = f"<h2>Tumor Characterization Report</h2>"
        html += f"<p><b>File:</b> {segmented_volume}</p>"
        html += f"<p><b>Volume:</b> {shape_features.get('volume_mm3', 'N/A')} mm³</p>"
        if visualization_path:
            html += f'<img src="{visualization_path}" width="400"/>'
        return html

    return str(report)
