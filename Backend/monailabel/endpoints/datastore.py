# Copyright (c) MONAI Consortium
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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


logger = logging.getLogger(__name__)
train_tasks: List = []
train_process: Dict = dict()

router = APIRouter(
    prefix="/datastore",
    tags=["Datastore"],
    responses={404: {"description": "Not found"}},
)


Radiology_path = Path("radiology")  
GENERAL_DATA_DIR = Radiology_path/"General_Data"
LABEL_DATA_DIR = Radiology_path / "Labels"

class ResultType(str, Enum):
    train = "train"
    stats = "stats"
    all = "all"


def datastore(output: Optional[ResultType] = None):
    d: Datastore = app_instance().datastore()
    output = output if output else ResultType.stats

    logger.debug(f"output type: {output}")
    if output == ResultType.all:
        return d.json()
    if output == ResultType.train:
        return d.datalist()
    return d.status()


def add_image(
    background_tasks: BackgroundTasks,
    image: Optional[str] = None,
    params: str = Form("{}"),
    file: UploadFile = File(...),
    user: Optional[str] = None,
):
    logger.info(f"Image: {image}; File: {file}; params: {params}")
    file_ext = "".join(pathlib.Path(file.filename).suffixes) if file.filename else ".nii.gz"

    image_id = image if image else os.path.basename(file.filename).replace(file_ext, "")
    image_file = tempfile.NamedTemporaryFile(suffix=file_ext).name

    with open(image_file, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        background_tasks.add_task(remove_file, image_file)

    instance: MONAILabelApp = app_instance()
    save_params: Dict[str, Any] = json.loads(params) if params else {}
    if user:
        save_params["user"] = user
    image_id = instance.datastore().add_image(image_id, image_file, save_params)
    return {"image": image_id}


def remove_image(id: str, user: Optional[str] = None):
    logger.info(f"Removing Image: {id} by {user}")
    instance: MONAILabelApp = app_instance()
    instance.datastore().remove_image(id)
    return {}


def save_label(
    background_tasks: BackgroundTasks,
    image: str,
    params: str = Form("{}"),
    tag: str = DefaultLabelTag.FINAL.value,
    label: UploadFile = File(...),
    user: Optional[str] = None,
):
    logger.info(f"Saving Label for {image} for tag: {tag} by {user}")
    file_ext = "".join(pathlib.Path(label.filename).suffixes) if label.filename else ".nii.gz"
    label_file = tempfile.NamedTemporaryFile(suffix=file_ext).name
    tag = tag if tag else DefaultLabelTag.FINAL.value

    with open(label_file, "wb") as buffer:
        shutil.copyfileobj(label.file, buffer)
        background_tasks.add_task(remove_file, label_file)

    instance: MONAILabelApp = app_instance()
    save_params: Dict[str, Any] = json.loads(params) if params else {}
    logger.info(f"Save Label params: {params}")

    label_id = instance.datastore().save_label(image, label_file, tag, save_params)
    res = instance.on_save_label(image, label_id)
    res = res if res else {}
    res.update(
        {
            "image": image,
            "label": label_id,
        }
    )
    return res


def remove_label(id: str, tag: str, user: Optional[str] = None):
    logger.info(f"Removing Label: {id} by {user}")
    instance: MONAILabelApp = app_instance()
    instance.datastore().remove_label(id, tag)
    return {}


def download_image(image: str, check_only=False, check_sum=None):
    instance: MONAILabelApp = app_instance()
    image = instance.datastore().get_image_uri(image)
    if not os.path.isfile(image):
        raise HTTPException(status_code=404, detail="Image NOT Found")

    if check_only:
        if check_sum:
            fields = check_sum.split(":")
            algo = "SHA256" if len(fields) == 1 else fields[0]
            digest = check_sum.lstrip(algo + ":") if len(fields) > 1 else check_sum
            if digest != file_checksum(image, algo=algo):
                raise HTTPException(status_code=404, detail="Image NOT Found (checksum failed)")
        return {}
    return FileResponse(image, media_type=get_mime_type(image), filename=os.path.basename(image))


def download_label(label: str, tag: str, check_only=False):
    instance: MONAILabelApp = app_instance()
    label = instance.datastore().get_label_uri(label, tag)
    if not os.path.isfile(label):
        raise HTTPException(status_code=404, detail="Label NOT Found")

    if check_only:
        return {}
    return FileResponse(label, media_type=get_mime_type(label), filename=os.path.basename(label))


def get_image_info(image: str):
    instance: MONAILabelApp = app_instance()
    return instance.datastore().get_image_info(image)


def update_image_info(image: str, info: str = Form("{}"), user: Optional[str] = None):
    logger.info(f"Update Image Info: {image} by {user}")

    instance: MONAILabelApp = app_instance()
    i = json.loads(info)
    if user:
        i["user"] = user
    return instance.datastore().update_image_info(image, i)


def get_label_info(label: str, tag: str):
    instance: MONAILabelApp = app_instance()
    return instance.datastore().get_label_info(label, tag)


def update_label_info(label: str, tag: str, info: str = Form("{}"), user: Optional[str] = None):
    logger.info(f"Update Label Info: {label} for {tag} by {user}")

    instance: MONAILabelApp = app_instance()
    i = json.loads(info)
    if user:
        i["user"] = user
    return instance.datastore().update_label_info(label, tag, i)


def download_dataset(limit_cases: Optional[int] = None):
    instance: MONAILabelApp = app_instance()
    path = instance.datastore().get_dataset_archive(limit_cases)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="ZIP archive NOT Found")
    return FileResponse(path, media_type=get_mime_type(path), filename="dataset.zip")

def zero_pad_label(instance_uid: str, start_index: int, end_index: int, target_shape: tuple):
    print("111111111111")
    label_path = LABEL_DATA_DIR / f"{instance_uid}.nii.gz"
    print("2222222222222",label_path)
    if not label_path.exists():
        raise FileNotFoundError(f"Label NIfTI file not found: {label_path}")
    
    label_img = nib.load(str(label_path))
    print("3333333333333333",label_img)
    label_data = label_img.get_fdata()
    flipped_label = label_data[:, :, ::-1]  # Reverse along the third dimension
    padded_label = np.zeros(target_shape)
    print("4444444444444444444444444")
    # Ensure dimensions match
    padded_label[:, :, start_index:end_index+1] = flipped_label[:, :, :end_index-start_index+1]
    print("5555555555555555",padded_label.shape)
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
        if not nii_file.exists():
            raise HTTPException(status_code=404, detail=f"NIfTI file not found for series UID: {series_uid}")
        
        # Load NIfTI
        nii_img = nib.load(str(nii_file))
        nii_data = nii_img.get_fdata()
        affine = nii_img.affine
        num_slices = nii_data.shape[2]
        print("nii_img",nii_img)
        slice_z_positions = [nib.affines.apply_affine(affine, [0, 0, z])[2] for z in range(num_slices)]
        
        # Get DICOM label path
        dicom_label_dir = instance.datastore().get_label_dir_dcm(instance_uid)
        dicom_label_path = None
        
        if Path(dicom_label_dir).exists() and Path(dicom_label_dir).is_dir():
            dicom_files = list(Path(dicom_label_dir).glob("*.dcm"))
            if not dicom_files:
                raise HTTPException(status_code=404, detail=f"No DICOM files found in directory for instance UID: {instance_uid}")
            dicom_label_path = dicom_files[0]  # Selecting the first DICOM file
        else:
            raise HTTPException(status_code=404, detail=f"DICOM label directory not found for instance UID: {instance_uid}")
        print("dicom path ",dicom_label_path)
        # Load DICOM SEG
        seg = pydicom.dcmread(dicom_label_path)
        seg_z_positions = [
            float(frame.PlanePositionSequence[0].ImagePositionPatient[2])
            for frame in seg.PerFrameFunctionalGroupsSequence
        ]
        
        # Compute start/end indices
        start_z_seg, end_z_seg = min(seg_z_positions), max(seg_z_positions)
        start_index = np.argmin(np.abs(np.array(slice_z_positions) - start_z_seg))
        end_index = np.argmin(np.abs(np.array(slice_z_positions) - end_z_seg))
        print("start   end   ",start_index ,end_index)
        # Zero pad label
        target_shape = nii_data.shape
        print("target shape",target_shape)
        padded_label = zero_pad_label(instance_uid, start_index, end_index, target_shape)
        print("is that done???????????????????")
        results[series_uid] = {
        "start_index": int(start_index),
        "end_index": int(end_index),
        "padded_label_shape": tuple(padded_label.shape) 
        }

    return results

@router.post("/labels", summary=f"{RBAC_USER} Download Labels")
async def api_download_labels(
    series_uids: List[str],
    task_name: str,  # Now a parameter like in the images endpoint
    user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_USER))
):
    # Define directories and mapping file path
    radiology_path = Path("radiology")
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
        entry.get("series_uid")
        for entry in instance_mapping.values()
        if entry.get("series_uid") and entry.get("task_name") == task_name
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

    return {
        "downloaded_files": downloaded_files,
        "skipped_files": skipped_files
    }

@router.post("/images", summary=f"{RBAC_USER}Download Multiple Images")
async def api_download_images(
    images: List[str],  # List of series UIDs
    task_name: str,  # Task name for filtering
    user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_USER)),
):
    # Define directories and mapping file path
    radiology_path = Path("radiology")  
    target_dir = radiology_path/"General_Data"
    mapping_path = radiology_path/"NN_Unet\instance_mapping.json"

    # Ensure the target directory exists
    os.makedirs(target_dir, exist_ok=True)

    downloaded_files = []
    skipped_files = []

    # Load the instance mapping from JSON if it exists, otherwise, initialize an empty dictionary.
    if os.path.exists(mapping_path):
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
        entry.get("series_uid")  # Use .get() to avoid KeyError
        for entry in instance_mapping.values()
        if entry.get("series_uid") and entry.get("task_name") == task_name
    }

    for series_uid in images:
        # Construct the target file path
        target_file = os.path.join(target_dir, f"{series_uid}.nii.gz")

        # Check if the file exists in the target directory
        if os.path.exists(target_file):
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
        downloaded_files.append(target_file)

      
    # Save the updated mapping back to the JSON file.
    mapping_dir = os.path.dirname(mapping_path)
    os.makedirs(mapping_dir, exist_ok=True)
    with open(mapping_path, "w") as f:
        json.dump(instance_mapping, f, indent=2)

    return {
        "message": "Download operation completed.",
        "downloaded_files": downloaded_files,
        "skipped_files": skipped_files,
    }


@router.get("/", summary=f"{RBAC_USER}Get All Images/Labels from datastore")
async def api_datastore(
    output: Optional[ResultType] = None,
    user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_USER)),
):
    return datastore(output)


@router.put("/", summary=f"{RBAC_ANNOTATOR}Upload new Image", include_in_schema=False, deprecated=True)
@router.put("/image", summary=f"{RBAC_ANNOTATOR}Upload new Image")
async def api_add_image(
    background_tasks: BackgroundTasks,
    image: Optional[str] = None,
    params: str = Form("{}"),
    file: UploadFile = File(...),
    user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_ANNOTATOR)),
):
    return add_image(background_tasks, image, params, file, user.username)


@router.delete(
    "/", summary=f"{RBAC_ADMIN}Remove Image and corresponding labels", include_in_schema=False, deprecated=True
)
@router.delete("/image", summary=f"{RBAC_ADMIN}Remove Image and corresponding labels")
async def api_remove_image(id: str, user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_ADMIN))):
    return remove_image(id, user.username)


@router.head("/image", summary=f"{RBAC_USER}Check If Image Exists")
async def api_check_image(
    image: str,
    check_sum: Optional[str] = None,
    user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_USER)),
):
    return download_image(image, check_only=True, check_sum=check_sum)


@router.get("/image", summary=f"{RBAC_USER}Download Image")
async def api_download_image(image: str, user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_USER))):
    return download_image(image)


@router.get("/image/info", summary=f"{RBAC_USER}Get Image Info")
async def api_get_image_info(image: str, user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_USER))):
    return get_image_info(image)


@router.put("/image/info", summary=f"{RBAC_ANNOTATOR}Update Image Info")
async def api_put_image_info(
    image: str,
    info: str = Form("{}"),
    user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_ANNOTATOR)),
):
    return update_image_info(image, info, user.username)


@router.put("/label", summary=f"{RBAC_ANNOTATOR}Save Finished Label")
async def api_save_label(
    background_tasks: BackgroundTasks,
    image: str,
    params: str = Form("{}"),
    tag: str = DefaultLabelTag.FINAL.value,
    label: UploadFile = File(...),
    user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_ANNOTATOR)),
):
    return save_label(background_tasks, image, params, tag, label, user.username)


@router.delete("/label", summary=f"{RBAC_ADMIN}Remove Label")
async def api_remove_label(id: str, tag: str, user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_ADMIN))):
    return remove_label(id, tag, user.username)


@router.head("/label", summary=f"{RBAC_USER}Check If Label Exists")
async def api_check_label(image: str, tag: str, user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_USER))):
    return download_label(image, tag, check_only=True)


@router.get("/label", summary=f"{RBAC_USER}Download Label")
async def api_download_label(label: str, tag: str, user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_USER))):
    return download_label(label, tag)


@router.get("/label/info", summary=f"{RBAC_USER}Get Label Info")
async def api_get_label_info(label: str, tag: str, user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_USER))):
    return get_label_info(label, tag)


@router.put("/label/info", summary=f"{RBAC_ANNOTATOR}Update Label Info")
async def api_put_label_info(
    label: str,
    tag: str,
    info: str = Form("{}"),
    user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_ANNOTATOR)),
):
    return update_label_info(label, tag, info, user.username)


@router.put("/updatelabelinfo", summary=f"{RBAC_ANNOTATOR}Update label info", include_in_schema=False, deprecated=True)
async def api_update_label_info(
    label: str,
    tag: str,
    params: str = Form("{}"),
    user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_ANNOTATOR)),
):
    return update_label_info(label, tag, params, user.username)


@router.get("/dataset", summary=f"{RBAC_ANNOTATOR}Download full dataset as ZIP archive")
async def api_download_dataset(
    limit_cases: Optional[int] = None,
    user: User = Depends(RBAC(settings.MONAI_LABEL_AUTH_ROLE_ANNOTATOR)),
):
    return download_dataset(limit_cases)
