# endpoints/my_endpoints_datastore.py

import json
import logging
import os
import pathlib
import shutil
import tempfile
from enum import Enum
from typing import Any, Dict, List, Optional
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile,Body
from fastapi.background import BackgroundTasks
from fastapi.responses import FileResponse
import nibabel as nib
import numpy as np
import pydicom

from monailabel.config import RBAC_ADMIN, RBAC_ANNOTATOR, RBAC_USER, settings
from monailabel.endpoints.user.auth import RBAC, User
from monailabel.interfaces.app import MONAILabelApp
from monailabel.interfaces.datastore import Datastore, DefaultLabelTag
from monailabel.interfaces.utils.app import app_instance
from monailabel.utils.others.generic import file_checksum, get_mime_type, remove_file
import os
import json
import shutil
import logging
from pathlib import Path
from typing import Dict, List

import numpy as np
import nibabel as nib
import pydicom

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse

from monailabel.interfaces.app import MONAILabelApp
from monailabel.config import RBAC_USER, settings
from monailabel.endpoints.user.auth import RBAC, User
from monailabel.interfaces.utils.app import app_instance
from monailabel.utils.others.generic import file_checksum, get_mime_type

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/datastore",
    tags=["Datastore"],
)

Radiology_path = Path("/apps/radiology")
GENERAL_DATA_DIR = Radiology_path / "General_Data"
LABEL_DATA_DIR = Radiology_path / "Labels"


def download_image(image: str, check_only=False, check_sum=None):
    instance: MONAILabelApp = app_instance()
    image_path = instance.datastore().get_image_uri(image)
    if not os.path.isfile(image_path):
        raise HTTPException(status_code=404, detail="Image NOT Found")

    if check_only:
        if check_sum:
            fields = check_sum.split(":")
            algo = "SHA256" if len(fields) == 1 else fields[0]
            digest = check_sum.lstrip(algo + ":") if len(fields) > 1 else check_sum
            if digest != file_checksum(image_path, algo=algo):
                raise HTTPException(status_code=404, detail="Image NOT Found (checksum failed)")
        return {}
    return FileResponse(image_path, media_type=get_mime_type(image_path), filename=os.path.basename(image_path))


def download_label(label: str, tag: str, check_only=False):
    instance: MONAILabelApp = app_instance()
    label_path = instance.datastore().get_label_uri(label, tag)
    if not os.path.isfile(label_path):
        raise HTTPException(status_code=404, detail="Label NOT Found")

    if check_only:
        return {}
    return FileResponse(label_path, media_type=get_mime_type(label_path), filename=os.path.basename(label_path))


def zero_pad_label(instance_uid: str, start_index: int, end_index: int, target_shape: tuple):
    print("111111111111")
    label_path = LABEL_DATA_DIR / f"{instance_uid}.nii.gz"
    print("2222222222222", label_path)
    if not label_path.exists():
        raise FileNotFoundError(f"Label NIfTI file not found: {label_path}")

    label_img = nib.load(str(label_path))
    print("3333333333333333", label_img)
    label_data = label_img.get_fdata()
    flipped_label = label_data[:, :, ::-1]  # Reverse along the third dimension
    padded_label = np.zeros(target_shape)
    print("4444444444444444444444444")
    # Ensure dimensions match
    padded_label[:, :, start_index : end_index + 1] = flipped_label[:, :, : end_index - start_index + 1]
    print("5555555555555555", padded_label.shape)
    padded_label_img = nib.Nifti1Image(padded_label, label_img.affine, label_img.header)
    nib.save(padded_label_img, str(label_path))
    return padded_label


@router.post("/process")
def process_data(series_instance_dict: Dict = Body(...)):
    instance: MONAILabelApp = app_instance()
    results = {}
    print("enter pro")
    for series_uid, instance_uid in series_instance_dict.items():
        # Locate NIfTI file
        nii_file = GENERAL_DATA_DIR / f"{series_uid}.nii.gz"
        print("Looking for NIfTI at:", nii_file)

        if not nii_file.exists():
            raise HTTPException(status_code=404, detail=f"NIfTI file not found for series UID: {series_uid}")

        # Load NIfTI
        nii_img = nib.load(str(nii_file))
        nii_data = nii_img.get_fdata()
        affine = nii_img.affine
        num_slices = nii_data.shape[2]
        print("nii_img", nii_img)
        slice_z_positions = [nib.affines.apply_affine(affine, [0, 0, z])[2] for z in range(num_slices)]

        # Get DICOM label path
        dicom_label_dir = instance.datastore().get_label_dir_dcm(instance_uid)
        dicom_label_path = None

        if Path(dicom_label_dir).exists() and Path(dicom_label_dir).is_dir():
            dicom_files = list(Path(dicom_label_dir).glob("*.dcm"))
            if not dicom_files:
                raise HTTPException(
                    status_code=404, detail=f"No DICOM files found in directory for instance UID: {instance_uid}"
                )
            dicom_label_path = dicom_files[0]  # Selecting the first DICOM file
        else:
            raise HTTPException(status_code=404, detail=f"DICOM label directory not found for instance UID: {instance_uid}")
        print("dicom path ", dicom_label_path)
        # Load DICOM SEG
        seg = pydicom.dcmread(dicom_label_path)
        seg_z_positions = [
            float(frame.PlanePositionSequence[0].ImagePositionPatient[2]) for frame in seg.PerFrameFunctionalGroupsSequence
        ]

        # Compute start/end indices
        start_z_seg, end_z_seg = min(seg_z_positions), max(seg_z_positions)
        start_index = np.argmin(np.abs(np.array(slice_z_positions) - start_z_seg))
        end_index = np.argmin(np.abs(np.array(slice_z_positions) - end_z_seg))
        print("start   end   ", start_index, end_index)
        # Zero pad label
        target_shape = nii_data.shape
        print("target shape", target_shape)
        padded_label = zero_pad_label(instance_uid, start_index, end_index, target_shape)
        print("is that done???????????????????")
        results[series_uid] = {
            "start_index": int(start_index),
            "end_index": int(end_index),
            "padded_label_shape": tuple(padded_label.shape),
        }

    return results


@router.post("/labels", summary=f"{RBAC_USER} Download Labels")
async def api_download_labels(
    series_uids: List[str],
    task_name: str,  # Now a parameter like in the images endpoint
    user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_USER)),
):
    # Define directories and mapping file path
    radiology_path = Path("/apps/radiology")
    target_dir = radiology_path / "Labels"
    mapping_path = radiology_path / "NN_Unet" / "instance_mapping.json"

    # Ensure the target directory exists
    os.makedirs(target_dir, exist_ok=True)
    mapping_path.parent.mkdir(parents=True, exist_ok=True)

    downloaded_files = []
    skipped_files = []

    # Load the instance mapping from JSON if it exists; otherwise, initialize an empty mapping.
    if mapping_path.exists():
        try:
            with open(mapping_path, "r") as f:
                instance_mapping = json.load(f)
        except json.JSONDecodeError:
            logger.warning("JSON decode error in instance_mapping.json; starting with an empty mapping.")
            instance_mapping = {}
    else:
        instance_mapping = {}

    # Optionally log any mapping entries that might be missing required keys
    for key, entry in instance_mapping.items():
        if "series_uid" not in entry:
            logger.warning(f"Mapping entry '{key}' is missing 'series_uid': {entry}")

    # Extract series UIDs already registered for the provided task name
    existing_series_uids = {
        entry.get("series_uid") for entry in instance_mapping.values() if entry.get("series_uid") and entry.get("task_name") == task_name
    }

    for series_uid in series_uids:
        target_file = target_dir / f"{series_uid}.nii.gz"

        # Check if the file already exists on disk
        if target_file.exists():
            logger.info(f"File already exists in Labels folder; skipping download: {target_file}")
            skipped_files.append(series_uid)
            continue

        # Check if the series UID is already in the mapping for this task
        if series_uid in existing_series_uids:
            logger.info(f"Series UID {series_uid} already exists in instance mapping for task '{task_name}'; skipping download.")
            skipped_files.append(series_uid)
            continue

        # Download the label file (assumed to be returned as a FileResponse)
        file_response = download_image(series_uid)
        if isinstance(file_response, FileResponse):
            # Move or copy the downloaded file to the target directory
            shutil.copy(file_response.path, target_file)
            downloaded_files.append(str(target_file))
        else:
            logger.error(f"Failed to download label for series UID: {series_uid}")
            skipped_files.append(series_uid)

    # Save the updated instance mapping back to the JSON file.
    with open(mapping_path, "w") as f:
        json.dump(instance_mapping, f, indent=2)

    return {"downloaded_files": downloaded_files, "skipped_files": skipped_files}


@router.post("/images", summary=f"{RBAC_USER}Download Multiple Images")
async def api_download_images(
    images: List[str],  # List of series UIDs
    task_name: str,  # Task name for filtering
    user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_USER)),
):
    # Define directories and mapping file path
    radiology_path = Path("/apps/radiology")
    target_dir = radiology_path / "General_Data"
    mapping_path = radiology_path / "NN_Unet" / "instance_mapping.json"

    # Ensure the target directory exists
    os.makedirs(target_dir, exist_ok=True)

    downloaded_files = []
    skipped_files = []

    # Load the instance mapping from JSON if it exists, otherwise, initialize an empty dictionary.
    if mapping_path.exists():
        try:
            with open(mapping_path, "r") as f:
                instance_mapping = json.load(f)
        except json.JSONDecodeError:
            logger.warning("JSON decode error in instance_mapping.json, starting with an empty mapping.")
            instance_mapping = {}
    else:
        instance_mapping = {}

    # Debug: Log missing series_uid entries
    for key, entry in instance_mapping.items():
        if "series_uid" not in entry:
            logger.warning(f"Entry '{key}' is missing 'series_uid': {entry}")

    # Extract all series UIDs that already exist for the given task_name (handling missing keys safely)
    existing_series_uids = {
        entry.get("series_uid") for entry in instance_mapping.values() if entry.get("series_uid") and entry.get("task_name") == task_name
    }

    for series_uid in images:
        # Construct the target file path
        target_file = target_dir / f"{series_uid}.nii.gz"

        # Check if the file exists in the target directory
        if target_file.exists():
            logger.info(f"File already exists in target directory, skipping download: {target_file}")
            skipped_files.append(series_uid)
            continue

        # Check if the series UID is already mapped for this task
        if series_uid in existing_series_uids:
            logger.info(f"Series UID {series_uid} already exists for task {task_name} in instance mapping, skipping download.")
            skipped_files.append(series_uid)
            continue

        # Download the image since it wasn’t found in either place
        file_response = download_image(series_uid)
        shutil.copy(file_response.path, target_file)
        downloaded_files.append(str(target_file))

    # Save the updated mapping back to the JSON file.
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    with open(mapping_path, "w") as f:
        json.dump(instance_mapping, f, indent=2)

    return {"message": "Download operation completed.", "downloaded_files": downloaded_files, "skipped_files": skipped_files}
