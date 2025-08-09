from fastapi import APIRouter, HTTPException, Body
import os
from pathlib import Path
import nibabel as nib
import numpy as np
import logging


# Configure logging to display timestamps, log level and messages
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# Create APIRouter instance for normalization and alignment endpoints
router = APIRouter(
    prefix="/normalize&align",  # Base URL path for all endpoints in this router
    tags=["Normalize & Align"],  # OpenAPI tag for documentation grouping
    responses={404: {"description": "Not found"}},  # Default error response
)

# Define data paths using pathlib for cross-platform compatibility
radiology_path = os.getenv("MONAI_LABEL_APP_DIR", "/apps/radiology")
radiology_path = Path(radiology_path)
DATA_FOLDER = radiology_path / "General_Data"  # Folder containing medical imaging data
LABEL_FOLDER = radiology_path / "Labels"  # Folder containing segmentation labels


def get_nifti_attributes(nifti_file: str) -> tuple:
    """
    Extracts key attributes from a NIfTI file.

    Parameters:
    - nifti_file: Path to the NIfTI file

    Returns:
    - tuple: (NIfTI image object, affine matrix, voxel spacing, origin coordinates)
    """
    logging.info(f"Extracting attributes from {nifti_file}")
    img = nib.load(nifti_file)
    affine = img.affine  # Affine transformation matrix
    header = img.header
    spacing = header.get_zooms()  # Voxel dimensions (x,y,z spacing)
    origin = affine[:3, 3]  # Origin coordinates in mm space
    return img, affine, spacing, origin


def check_and_normalize_label(label_file: str) -> dict:
    """
    Validates and normalizes a segmentation label file.
    Performs two checks:
    1. If the label is a binary mask (0s and 1s)
    2. If the label values are in [0,1] range

    If neither condition is met, normalizes the data to [0,1] range.

    Parameters:
    - label_file: Path to the label NIfTI file

    Returns:
    - dict: Normalization status information
    """
    logging.info(f"Checking normalization for {label_file}")
    label_img = nib.load(label_file)
    label_data = label_img.get_fdata()
    min_val, max_val = np.min(label_data), np.max(label_data)

    # Check for binary mask (only 0 and 1 values)
    if np.all(np.logical_or(label_data == 0, label_data == 1)):
        logging.info(f"{label_file} is already a binary mask.")
        return {"file": label_file, "status": "Already binary"}

    # Check if already normalized
    if min_val >= 0 and max_val <= 1:
        logging.info(f"{label_file} is already normalized.")
        return {"file": label_file, "status": "Already normalized"}

    # Perform normalization if needed
    logging.warning(f"{label_file} is not normalized. Normalizing now...")
    normalized_label_data = (label_data - min_val) / (max_val - min_val)
    normalized_label_img = nib.Nifti1Image(
        normalized_label_data, label_img.affine, label_img.header
    )
    nib.save(normalized_label_img, label_file)
    logging.info(f"Normalization complete for {label_file}.")
    return {"file": label_file, "status": "Normalized"}


def match_label_to_data(data_file: str, label_file: str) -> dict:
    """
    Ensures a label file matches the spatial characteristics of its corresponding data file.
    Compares:
    - Affine transformation matrix
    - Voxel spacing
    - Origin coordinates

    If mismatches are found, resamples the label to match the data's space.

    Parameters:
    - data_file: Path to the reference data NIfTI
    - label_file: Path to the label NIfTI to align

    Returns:
    - dict: Alignment status information
    """
    logging.info(f"Checking alignment for {data_file} and {label_file}")
    data_img, data_affine, data_spacing, data_origin = get_nifti_attributes(data_file)
    label_img, label_affine, label_spacing, label_origin = get_nifti_attributes(
        label_file
    )

    # Check for spatial alignment
    if (
        np.allclose(data_affine, label_affine)
        and np.allclose(data_spacing, label_spacing)
        and np.allclose(data_origin, label_origin)
    ):
        logging.info(f"{label_file} is already aligned with {data_file}.")
        return {"data": data_file, "label": label_file, "status": "Already aligned"}

    # Resample label if mismatch found
    logging.warning(
        f"Misalignment detected! Aligning {label_file} to match {data_file}..."
    )
    resampled_label_img = nib.Nifti1Image(
        label_img.get_fdata(), data_affine, data_img.header
    )
    nib.save(resampled_label_img, label_file)
    logging.info(f"Alignment complete for {label_file}.")
    return {"data": data_file, "label": label_file, "status": "Aligned"}


@router.get("/normalize-labels", summary="Normalize all label files")
def normalize_labels():
    """
    Endpoint to normalize all label files in the LABEL_FOLDER.

    Processing steps:
    1. Scans the label directory for .nii.gz files
    2. For each file:
       - Checks if already normalized/binary
       - Normalizes if needed
    3. Returns processing status for all files

    Returns:
    - List of dictionaries with normalization results
    """
    logging.info("Starting label normalization for all files in the labels folder.")
    label_files = [
        os.path.join(LABEL_FOLDER, f)
        for f in os.listdir(LABEL_FOLDER)
        if f.endswith(".nii.gz")
    ]
    results = [check_and_normalize_label(label) for label in label_files]
    logging.info("Label normalization process completed.")
    return results


@router.post("/align-labels", summary="Align labels to their corresponding data files")
def align_labels(data_to_label: dict = Body(...)):
    """
    Endpoint to align label files to match their corresponding data files.

    Expects a JSON body mapping data filenames to label filenames:
    {
        "data_file1.nii.gz": "label1.nii.gz",
        "data_file2.nii.gz": "label2.nii.gz"
    }

    Processing steps:
    1. For each data-label pair:
       - Compares spatial attributes
       - Resamples label if mismatch found
    2. Returns alignment status for all pairs

    Parameters:
    - data_to_label: Dictionary mapping data filenames to label filenames

    Returns:
    - List of dictionaries with alignment results
    """
    logging.info("Starting label alignment process.")
    results = [
        match_label_to_data(
            os.path.join(DATA_FOLDER, data + ".nii.gz"),
            os.path.join(LABEL_FOLDER, label + ".nii.gz"),
        )
        for data, label in data_to_label.items()
    ]
    logging.info("Label alignment process completed.")
    return results
