# Handle nnUNet

## Step 1: Add API in Monai Label Server

1. Navigate to the path of your Monai Label installation, typically:

   ```
    C:\Users\"your user name"\AppData\Local\Programs\Python\Python39\Lib\site-packages\monailabel   ```
   ```

   Replace `Python39` with your Python version.

2. Create a new file named `handle_nnunet.py` in the `endpoints` directory.

3. Add the following code to `handle_nnunet.py`:

   ```python
    import os
    import json
    import shutil
    from pathlib import Path
    from typing import Dict, Tuple

    import nibabel as nib
    import numpy as np
    import pydicom
    import SimpleITK as sitk
    from fastapi import APIRouter, HTTPException, Body
    from pydantic import BaseModel

    router = APIRouter(
        prefix="/NN_UNet",
        tags=["nnUNet"],
        responses={404: {"description": "Not found"}},
    )

    # Base folders 
    radiology_path = Path("radiology")  
    BASE_PATH =radiology_path/"NN_Unet"
    LABELS_PATH = radiology_path/"Labels"
    GENERAL_DATA_PATH = radiology_path/"General_Data"
    MAPPING_FILE = BASE_PATH / "instance_mapping.json"

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
        index = len(list(NNUNET_RAW.glob("Dataset*"))) + 1
        dataset_name = f"Dataset{index:03d}_{task_name}"
        
        raw_folder = NNUNET_RAW / dataset_name
        preprocessed_folder = NNUNET_PREPROCESSED / dataset_name
        results_folder = NNUNET_RESULTS / dataset_name

        for folder in [raw_folder, preprocessed_folder, results_folder]:
            folder.mkdir(parents=True, exist_ok=True)

        # Ensure required subfolders exist in the raw folder.
        for sub in ["imagesTr", "imagesTs", "labelsTr"]:
            (raw_folder / sub).mkdir(parents=True, exist_ok=True)
        return raw_folder, preprocessed_folder, results_folder

    def ensure_extra_folders(dataset_folder_name: str):
        """
        Ensures that the task folders exist in nnUNet_preprocessed and nnUNet_results
        for a given dataset folder name.
        """
        print("enter the ensure_extra_folders")
        preprocessed_folder = NNUNET_PREPROCESSED / dataset_folder_name
        results_folder = NNUNET_RESULTS / dataset_folder_name
        preprocessed_folder.mkdir(parents=True, exist_ok=True)
        results_folder.mkdir(parents=True, exist_ok=True)

    # Global mapping loaded from file.
    if MAPPING_FILE.exists():
        with open(MAPPING_FILE, "r") as f:
            instance_mapping = json.load(f)
    else:
        instance_mapping = {}

    def save_mapping(mapping: dict):
        with open(MAPPING_FILE, "w") as f:
            json.dump(mapping, f, indent=4)

    def find_image_file(series_instance_uid: str) -> Path:
        """
        Search in GENERAL_DATA_PATH for a file whose name starts with the given series_instance_uid.
        Returns the first matching file.
        """
        for file in GENERAL_DATA_PATH.iterdir():
            # Debug: print(file.name)
            if file.name.startswith(series_instance_uid):
                return file
        raise FileNotFoundError(f"No valid file found for Series Instance UID {series_instance_uid} in 'General Data'.")

    def find_label_file(instance_uid: str) -> Path:
        """
        Search in LABELS_PATH for a file with the given instance_uid and a supported extension.
        """
        for file in LABELS_PATH.iterdir():
            if file.name.startswith(instance_uid):
                return file
        raise FileNotFoundError(f"Label file with instance UID {instance_uid} not found in supported formats (.npy, .dcm)")

    # ------------------------------------------------------------------
    # Pydantic models for the input format.
    # ------------------------------------------------------------------
    class ImageEntry(BaseModel):
        instance_uid: str
        modality: str   # e.g. "ct", "mri", etc.
        training: bool  # True for training case; False for testing case

    class TaskCreationRequest(BaseModel):
        task_name: str
        channel_names: Dict[str, str]  # Provided once per task (used in dataset.json)
        labels: Dict[str, int]         # Provided once per task (used in dataset.json)
        # Each key is a series UID; its value holds the associated instance UID, modality, and training flag.
        images: Dict[str, ImageEntry]
    # ------------------------------------------------------------------
    # The API endpoint using the new input format.
    # ------------------------------------------------------------------
    @router.post("/create_task")
    def create_task(data: TaskCreationRequest = Body(...)):
        # Convert task name to uppercase for uniformity
        task_upper = data.task_name.upper()

        # Check if any cases for this task already exist in the global instance mapping.
        task_entries = [
            (case_id, info) for case_id, info in instance_mapping.items()
            if info.get("task_name", "").lower() == data.task_name.lower()
        ]
        
        if task_entries:
            # Try to find the correct dataset folder for the existing task.
            dataset_folder_name = None
            for case_id, info in task_entries:
                # Construct the dataset folder name using the mapping info.
                candidate = f"Dataset{int(case_id.split('_')[1]):03d}_{info.get('task_name')}"
                candidate_path = NNUNET_RAW / candidate
                if candidate_path.exists():
                    dataset_folder_name = candidate
                    break
            
            if dataset_folder_name:
                # Set folder paths for raw, preprocessed, and result datasets
                raw_folder = NNUNET_RAW / dataset_folder_name
                preprocessed_folder = NNUNET_PREPROCESSED / dataset_folder_name
                results_folder = NNUNET_RESULTS / dataset_folder_name
                ensure_extra_folders(dataset_folder_name)
                dataset_folder = raw_folder
                # Determine the next available case counter for new cases
                next_counter = max([int(case_id.split("_")[1]) for case_id, _ in task_entries]) + 1
            else:
                # If no dataset folder is found, create a new dataset folder
                last_task_index = len(list(NNUNET_RAW.glob("Dataset*"))) + 1
                dataset_folder_name = f"Dataset{last_task_index:03d}_{data.task_name}"
                raw_folder, preprocessed_folder, results_folder = ensure_all_task_folders(data.task_name)
                dataset_folder = raw_folder
                next_counter = 1
        else:
            # No previous cases exist for this task, create a new dataset folder
            last_task_index = len(list(NNUNET_RAW.glob("Dataset*"))) + 1
            dataset_folder_name = f"Dataset{last_task_index:03d}_{data.task_name}"
            raw_folder, preprocessed_folder, results_folder = ensure_all_task_folders(data.task_name)
            dataset_folder = raw_folder
            next_counter = 1
        
        num_training_new = 0
        num_testing_new = 0

        # Iterate over the provided image entries in the request
        for series_uid, entry in data.images.items():
            # Skip if the same series_uid and instance_uid already exist in the mapping
            if any(
                info.get("series_uid") == series_uid and 
                info.get("instance_uid") == entry.instance_uid and 
                info.get("task_name", "").lower() == data.task_name.lower()
                for info in instance_mapping.values()
            ):
                continue  # Skip duplicate cases

            # Check if a case for this series already exists (even if instance_uid differs)
            existing_case = None
            for case_id, info in instance_mapping.items():
                if info.get("series_uid") == series_uid and info.get("task_name", "").lower() == data.task_name.lower():
                    existing_case = case_id
                    break
            
            if existing_case:
                # Use the existing case identifier
                case_identifier = existing_case
                
                # Remove any old label file if updating an existing case
                labels_folder = dataset_folder / "labelsTr"
                if labels_folder.exists():
                    for file in labels_folder.glob(f"{case_identifier}*"):
                        try:
                            file.unlink()
                        except Exception as e:
                            pass  # Handle errors if necessary
                
                if entry.training:
                    num_training_new += 1
                else:
                    num_testing_new += 1
            else:
                # Create a new case identifier
                case_identifier = f"{task_upper}_{next_counter:03d}"
                next_counter += 1
                
                # Ensure dataset subfolders exist
                for sub in ["imagesTr", "imagesTs", "labelsTr"]:
                    (dataset_folder / sub).mkdir(parents=True, exist_ok=True)
                
                if entry.training:
                    num_training_new += 1
                else:
                    num_testing_new += 1

            # Determine image filename based on modality (MRI vs. others)
            modality_lower = entry.modality.lower()
            channel_code = "0001" if modality_lower == "mri" else "0000"
            new_image_filename = f"{case_identifier}_{channel_code}.nii.gz"
            target_img_folder = dataset_folder / ("imagesTr" if entry.training else "imagesTs")
            img_destination = target_img_folder / new_image_filename

            if not existing_case:
                # Locate the original image file and move it to the dataset folder
                try:
                    source_img_file = find_image_file(series_uid)
                except FileNotFoundError as e:
                    raise HTTPException(status_code=404, detail=str(e))
                
                if str(source_img_file).endswith(".nii.gz"):
                    try:
                        shutil.move(str(source_img_file), str(img_destination))
                    except Exception as e:
                        raise HTTPException(status_code=500, detail=f"Error moving image file {source_img_file}: {e}")
            
            new_label_filename = None
            if entry.training:
                # Move the corresponding label file if it exists
                try:
                    source_label_file = find_label_file(entry.instance_uid)
                except FileNotFoundError as e:
                    raise HTTPException(status_code=404, detail=str(e))
                
                new_label_filename = f"{case_identifier}.nii.gz"
                label_destination = dataset_folder / "labelsTr" / new_label_filename
                if str(source_label_file).endswith(".nii.gz"):
                    try:
                        shutil.move(str(source_label_file), str(label_destination))
                    except Exception as e:
                        raise HTTPException(status_code=500, detail=f"Error moving label file {source_label_file}: {e}")
            
            # Update the instance mapping with the new or modified case
            instance_mapping[case_identifier] = {
                "task_name": data.task_name,
                "series_uid": series_uid,
                "instance_uid": entry.instance_uid,
                "modality": entry.modality,
                "training": entry.training,
                "image_filename": new_image_filename,
                "label_filename": new_label_filename,
            }
        
        # Compute total training/testing counts for the task
        overall_training = sum(
            1 for info in instance_mapping.values()
            if info.get("task_name", "").lower() == data.task_name.lower() and info.get("training")
        )
        overall_testing = sum(
            1 for info in instance_mapping.values()
            if info.get("task_name", "").lower() == data.task_name.lower() and not info.get("training")
        )
        
        # Create dataset.json file for the task
        dataset_json = {
            "channel_names": data.channel_names,
            "labels": data.labels,
            "numTraining": overall_training,
            "numTesting": overall_testing,
            "file_ending": ".nii.gz",
            "overwrite_image_reader_writer": "SimpleITKIO"
        }
        dataset_json_path = dataset_folder / "dataset.json"
        
        try:
            with open(dataset_json_path, "w") as f:
                json.dump(dataset_json, f, indent=4)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error writing dataset.json: {e}")
        
        save_mapping(instance_mapping)
        
        return {"message": "Task created/updated successfully.", "dataset_folder": dataset_folder.name}

   ```

4. In the `app.py`, import the `handle_nnunet` file and add the following line:
   
   ```python
   app.include_router(handle_nnunet.router, prefix=settings.MONAI_LABEL_API_STR)
   ```

## Form of the Request Body

1. **Request Body**:

   ```json
    {
    "task_name": "brain",//Task Segmentation name 
    "channel_names": {//Depend on Modality of Data 
        "0": "CT",
        "1": "MRI"
    },
    "labels": {//Must Have Background for nn-Unet 
        "background": 0,
        "tumor": 1
    },
    "images": {
        "1.2.840.113845.13.5681.3264321649.22890278360": {//represent the Volume 
        "instance_uid": "2.25.258099372337726399227791255001726345546",//represent the Mask 
        "modality": "MRI",// modality --> MRI or CT 
        "training": true // Using in Traning 
        },
        "1.2.840.113845.13.5681.3264321649.22890278361": {//represent the Volume 
        "instance_uid": "2.25.258099372337726399227791255001726345547",//represent the Mask 
        "modality": "CT", // modality --> MRI or CT 
        "training": false //Using in Testing 
        }
    }
    }
   ```

