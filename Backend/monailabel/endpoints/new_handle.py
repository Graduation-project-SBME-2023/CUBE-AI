import logging
logger = logging.getLogger(__name__)
import os
import json
import shutil
from pathlib import Path
from typing import Dict, Tuple
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from storage_paths import MEDICAL_IMAGES_DIR, MEDICAL_LABELS_DIR, NNUNET_DIR

router = APIRouter(
    prefix="/NN_UNet",
    tags=["nnUNet"],
    responses={404: {"description": "Not found"}},
)

# Base folders (adjust these as needed)
BASE_PATH = NNUNET_DIR
LABELS_PATH = MEDICAL_LABELS_DIR
GENERAL_DATA_PATH = MEDICAL_IMAGES_DIR
MAPPING_FILE = BASE_PATH / "mappings" / "instance_mapping.json"
MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)

# Ensure nnUNet main folders exist
NNUNET_RAW = BASE_PATH / "nnUNet_raw"
NNUNET_PREPROCESSED = BASE_PATH / "nnUNet_preprocessed"
NNUNET_RESULTS = BASE_PATH / "nnUNet_results"

for folder in [NNUNET_RAW, NNUNET_PREPROCESSED, NNUNET_RESULTS]:
    folder.mkdir(parents=True, exist_ok=True)


def ensure_all_task_folders(task_name: str) -> Tuple[Path, Path, Path]:
    """
    Creates a new task folder (DatasetXXX_TaskName) in each of the main directories and
    returns a tuple with (raw_folder, preprocessed_folder, results_folder).
    """
    logger.info('enter the ensure_all_task_folders')
    index = len(list(NNUNET_RAW.glob("Dataset*"))) + 1
    dataset_name = f"Task{index:03d}_{task_name}"

    raw_folder = NNUNET_RAW / dataset_name
    preprocessed_folder = NNUNET_PREPROCESSED / dataset_name
    results_folder = NNUNET_RESULTS / dataset_name

    for folder in [raw_folder, preprocessed_folder, results_folder]:
        folder.mkdir(parents=True, exist_ok=True)

    # Ensure required subfolders exist in the raw folder.
    for sub in ["imagesTr", "imagesTs", "labelsTr"]:
        (raw_folder / sub).mkdir(parents=True, exist_ok=True)
    logger.info("exist the ensure_all_task_folders", raw_folder, preprocessed_folder, results_folder)
    return raw_folder, preprocessed_folder, results_folder


def ensure_extra_folders(dataset_folder_name: str):
    """
    Ensures that the task folders exist in nnUNet_preprocessed and nnUNet_results
    for a given dataset folder name.
    """
    logger.info("enter the ensure_extra_folders")
    preprocessed_folder = NNUNET_PREPROCESSED / dataset_folder_name
    results_folder = NNUNET_RESULTS / dataset_folder_name
    preprocessed_folder.mkdir(parents=True, exist_ok=True)
    results_folder.mkdir(parents=True, exist_ok=True)
    logger.info("exist the ensure_extra_folders", preprocessed_folder, results_folder)


# Global mapping loaded from file.
if MAPPING_FILE.exists():
    logger.info('Enter the if (mapping file exists)')
    with open(MAPPING_FILE, "r") as f:
        instance_mapping = json.load(f)
else:
    logger.info('Enter the else (mapping file not exists)')
    instance_mapping = {}


def save_mapping(mapping: dict):
    with open(MAPPING_FILE, "w") as f:
        json.dump(mapping, f, indent=4)


def find_image_file(series_instance_uid: str) -> Path:
    """
    Search in GENERAL_DATA_PATH for a file whose name starts with the given series_instance_uid.
    Returns the first matching file.
    """
    logger.info("enterh the fing image file and the series_instance_uid is", series_instance_uid)
    for file in GENERAL_DATA_PATH.iterdir():
        # Debug: logger.info(file.name)
        if file.name.startswith(series_instance_uid):
            logger.info("exit the find image file", file)
            return file
    raise FileNotFoundError(f"No valid file found for Series Instance UID {series_instance_uid} in 'General Data'.")


def find_label_file(instance_uid: str) -> Path:
    """
    Search in LABELS_PATH for a file with the given instance_uid and a supported extension.
    """
    logger.info("enter the find label file and the instance_uid is", instance_uid)
    for file in LABELS_PATH.iterdir():
        if file.name.startswith(instance_uid):
            logger.info("exit the find label file", file)
            return file
    raise FileNotFoundError(f"Label file with instance UID {instance_uid} not found in supported formats (.npy, .dcm)")


# ------------------------------------------------------------------
# Pydantic models for the input format.
# ------------------------------------------------------------------
class ImageEntry(BaseModel):
    instance_uid: str
    modality: str  # e.g. "ct", "mri", etc.
    training: bool  # True for training case; False for testing case


class TaskCreationRequest(BaseModel):
    task_name: str
    channel_names: Dict[str, str]  # Provided once per task (used in dataset.json)
    labels: Dict[str, int]  # Provided once per task (used in dataset.json)
    # Each key is a series UID; its value holds the associated instance UID, modality, and training flag.
    images: Dict[str, ImageEntry]


# ------------------------------------------------------------------
# The API endpoint using the new input format.
# ------------------------------------------------------------------
@router.post("/create_task")
def create_task(data: TaskCreationRequest = Body(...)):
    logger.info('-----------------------------------step1', data)
    task_upper = data.task_name.upper()
    logger.info('-----------------------------------step1', task_upper)

    # Check if any cases for this task already exist in the global mapping.
    task_entries = [
        (case_id, info) for case_id, info in instance_mapping.items()
        if info.get("task_name", "").lower() == data.task_name.lower()
    ]
    logger.info('-----------------------------------step3', task_entries)
    if task_entries:
        logger.info('-----------------------------------enter the if (existing task) -------')
        # Try to find the correct dataset folder for the existing task.
        dataset_folder_name = None
        for case_id, info in task_entries:
            # Build candidate dataset folder name from the mapping info.
            candidate = f"Dataset{int(case_id.split('_')[1]):03d}_{info.get('task_name')}"
            candidate_path = NNUNET_RAW / candidate
            if candidate_path.exists():
                dataset_folder_name = candidate
                logger.info('-----------------------------------found dataset folder:', dataset_folder_name)
                break
        if dataset_folder_name:
            raw_folder = NNUNET_RAW / dataset_folder_name
            preprocessed_folder = NNUNET_PREPROCESSED / dataset_folder_name
            results_folder = NNUNET_RESULTS / dataset_folder_name
            ensure_extra_folders(dataset_folder_name)
            dataset_folder = raw_folder
            next_counter = max([int(case_id.split("_")[1]) for case_id, _ in task_entries]) + 1
        else:
            logger.info('-----------------------------------No existing dataset folder found, creating new one')
            last_task_index = len(list(NNUNET_RAW.glob("Dataset*"))) + 1
            dataset_folder_name = f"Dataset{last_task_index:03d}_{data.task_name}"
            raw_folder, preprocessed_folder, results_folder = ensure_all_task_folders(data.task_name)
            dataset_folder = raw_folder
            next_counter = 1
    else:
        logger.info('-----------------------------------No task entries exist yet, creating new dataset')
        last_task_index = len(list(NNUNET_RAW.glob("Dataset*"))) + 1
        dataset_folder_name = f"Dataset{last_task_index:03d}_{data.task_name}"
        raw_folder, preprocessed_folder, results_folder = ensure_all_task_folders(data.task_name)
        dataset_folder = raw_folder
        next_counter = 1
    print("Dataset folder info:", dataset_folder_name, raw_folder, preprocessed_folder, results_folder, dataset_folder,
          next_counter)

    num_training_new = 0
    num_testing_new = 0

    for series_uid, entry in data.images.items():
        # Skip iteration if the same series_uid and instance_uid already exist for the task.
        logger.info('instance mapping valuess-----------', instance_mapping.values())
        if any(
                info.get("series_uid") == series_uid and
                info.get("instance_uid") == entry.instance_uid and
                info.get("task_name", "").lower() == data.task_name.lower()
                for info in instance_mapping.values()
        ):
            print(
                f"Skipping existing case: series_uid={series_uid}, instance_uid={entry.instance_uid}, task_name={data.task_name}")
            continue  # Do not process duplicate case

        # Check if a case for this series already exists (even if instance_uid differs).
        existing_case = None
        for case_id, info in instance_mapping.items():
            if info.get("series_uid") == series_uid and info.get("task_name", "").lower() == data.task_name.lower():
                existing_case = case_id
                logger.info(f"Found existing case: series_uid={series_uid}, task_name={data.task_name}, case_id={case_id}")
                break

        if existing_case:
            logger.info("Existing case found:", existing_case)
            case_identifier = existing_case
            # Remove any old label file for an update.
            labels_folder = dataset_folder / "labelsTr"
            if labels_folder.exists():
                for file in labels_folder.glob(f"{case_identifier}*"):
                    try:
                        file.unlink()
                    except Exception as e:
                        logger.info(f"Error deleting label file {file}: {e}")
            if entry.training:
                num_training_new += 1
            else:
                num_testing_new += 1
        else:
            # Create a new case identifier.
            case_identifier = f"{task_upper}_{next_counter:03d}"
            next_counter += 1
            # Ensure subfolders exist.
            for sub in ["imagesTr", "imagesTs", "labelsTr"]:
                (dataset_folder / sub).mkdir(parents=True, exist_ok=True)
            if entry.training:
                num_training_new += 1
            else:
                num_testing_new += 1
        logger.info("Case identifier:", case_identifier)

        modality_lower = entry.modality.lower()
        logger.info("Modality lower:", modality_lower)
        channel_code = "0001" if modality_lower == "mri" else "0000"
        logger.info("Channel code:", channel_code)
        new_image_filename = f"{case_identifier}_{channel_code}.nii.gz"
        logger.info("New image filename:", new_image_filename)
        target_img_folder = dataset_folder / ("imagesTr" if entry.training else "imagesTs")
        logger.info("Target image folder:", target_img_folder)
        img_destination = target_img_folder / new_image_filename
        logger.info("Image destination:", img_destination)

        if not existing_case:
            try:
                source_img_file = find_image_file(series_uid)
                logger.info("Source image fileeeeeeeeeeeeeeeeeee:", source_img_file)
            except FileNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))
            if str(source_img_file).endswith(".nii.gz"):
                try:
                    shutil.move(str(source_img_file), str(img_destination))
                    logger.info("Destination image fileeeeeeeeeeeeeeeeeee:", img_destination)
                except Exception as e:
                    raise HTTPException(status_code=500, detail=f"Error moving image file {source_img_file}: {e}")

        new_label_filename = None
        logger.info("Entry training flag:", entry.training)
        if entry.training:
            try:
                source_label_file = find_label_file(entry.instance_uid)
                logger.info('Source label fileeeeeeeeeeee:', source_label_file)
            except FileNotFoundError as e:
                raise HTTPException(status_code=404, detail=str(e))
            new_label_filename = f"{case_identifier}.nii.gz"
            label_destination = dataset_folder / "labelsTr" / new_label_filename
            if str(source_label_file).endswith(".nii.gz"):
                try:
                    shutil.move(str(source_label_file), str(label_destination))
                    logger.info("Destination label fileeeeeeeeeeeeeeeeeee:", label_destination)
                except Exception as e:
                    raise HTTPException(status_code=500, detail=f"Error moving label file {source_label_file}: {e}")

        logger.info("New label filename:", new_label_filename)
        # Update the instance mapping.
        instance_mapping[case_identifier] = {
            "task_name": data.task_name,
            "series_uid": series_uid,
            "instance_uid": entry.instance_uid,
            "modality": entry.modality,
            "training": entry.training,
            "image_filename": new_image_filename,
            "label_filename": new_label_filename,
        }
        logger.info("Updated instance mapping:", instance_mapping)

    overall_training = sum(
        1 for info in instance_mapping.values()
        if info.get("task_name", "").lower() == data.task_name.lower() and info.get("training")
    )
    overall_testing = sum(
        1 for info in instance_mapping.values()
        if info.get("task_name", "").lower() == data.task_name.lower() and not info.get("training")
    )
    logger.info("Overall training and testing counts:", overall_training, overall_testing)
    # Ensure "background" is always set to 0
    fixed_labels = {"background": 0}
    fixed_labels.update(data.labels)  # Merge user-provided labels while keeping "background": 0

    dataset_json = {
        "channel_names": data.channel_names,
        "labels": fixed_labels,
        "numTraining": overall_training,
        "numTesting": overall_testing,
        "file_ending": ".nii.gz",
        "overwrite_image_reader_writer": "SimpleITKIO"
    }
    dataset_json_path = dataset_folder / "dataset.json"
    logger.info("Dataset json path:", dataset_json_path)
    try:
        with open(dataset_json_path, "w") as f:
            json.dump(dataset_json, f, indent=4)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error writing dataset.json: {e}")

    save_mapping(instance_mapping)
    logger.info("Final instance mapping:", instance_mapping)

    return {
        "message": "Task created/updated successfully.",
        "dataset_folder": dataset_folder.name,
        "num_training": overall_training,
        "num_testing": overall_testing,
        "instance_mapping_update": instance_mapping
    }
