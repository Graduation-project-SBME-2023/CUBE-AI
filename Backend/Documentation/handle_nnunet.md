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
    from fastapi import APIRouter, HTTPException, Body
    from pydantic import BaseModel

    # ----------------------------
    # Router Configuration
    # ----------------------------
    router = APIRouter(
        prefix="/NN_UNet",
        tags=["nnUNet"],
        responses={404: {"description": "Not found"}},
    )

    # ----------------------------
    # Paths & Constants
    # ----------------------------
    radiology_path = Path("radiology")
    BASE_PATH = radiology_path / "NN_Unet"
    LABELS_PATH = radiology_path / "Labels"
    GENERAL_DATA_PATH = radiology_path / "General_Data"
    MAPPING_FILE = BASE_PATH / "instance_mapping.json"

    # nnUNet required folders
    NNUNET_RAW = BASE_PATH / "nnUNet_raw"
    NNUNET_PREPROCESSED = BASE_PATH / "nnUNet_preprocessed"
    NNUNET_RESULTS = BASE_PATH / "nnUNet_results"

    for folder in [NNUNET_RAW, NNUNET_PREPROCESSED, NNUNET_RESULTS]:
        folder.mkdir(parents=True, exist_ok=True)

    # ----------------------------
    # Folder Management Functions
    # ----------------------------
    def ensure_all_task_folders(task_name: str) -> Tuple[Path, Path, Path]:
        """Creates required dataset folders for a new task."""
        index = len(list(NNUNET_RAW.glob("Dataset*"))) + 1
        dataset_name = f"Dataset{index:03d}_{task_name}"

        raw_folder = NNUNET_RAW / dataset_name
        preprocessed_folder = NNUNET_PREPROCESSED / dataset_name
        results_folder = NNUNET_RESULTS / dataset_name

        for folder in [raw_folder, preprocessed_folder, results_folder]:
            folder.mkdir(parents=True, exist_ok=True)

        for sub in ["imagesTr", "imagesTs", "labelsTr"]:
            (raw_folder / sub).mkdir(parents=True, exist_ok=True)

        return raw_folder, preprocessed_folder, results_folder

    def ensure_extra_folders(dataset_folder_name: str):
        """Ensures folders exist for existing task in preprocessed and results folders."""
        (NNUNET_PREPROCESSED / dataset_folder_name).mkdir(parents=True, exist_ok=True)
        (NNUNET_RESULTS / dataset_folder_name).mkdir(parents=True, exist_ok=True)

    # ----------------------------
    # File & Mapping Management
    # ----------------------------
    if MAPPING_FILE.exists():
        with open(MAPPING_FILE, "r") as f:
            instance_mapping = json.load(f)
    else:
        instance_mapping = {}

    def save_mapping(mapping: dict):
        with open(MAPPING_FILE, "w") as f:
            json.dump(mapping, f, indent=4)

    def find_image_file(series_instance_uid: str) -> Path:
        """Finds image file in GENERAL_DATA_PATH using series UID prefix."""
        for file in GENERAL_DATA_PATH.iterdir():
            if file.name.startswith(series_instance_uid):
                return file
        raise FileNotFoundError(f"No file found for Series UID {series_instance_uid}.")

    def find_label_file(instance_uid: str) -> Path:
        """Finds label file in LABELS_PATH using instance UID prefix."""
        for file in LABELS_PATH.iterdir():
            if file.name.startswith(instance_uid):
                return file
        raise FileNotFoundError(f"No label found for Instance UID {instance_uid}.")

    # ----------------------------
    # Pydantic Models
    # ----------------------------
    class ImageEntry(BaseModel):
        instance_uid: str
        modality: str   # e.g., "ct", "mri"
        training: bool  # True = training, False = testing

    class TaskCreationRequest(BaseModel):
        task_name: str
        channel_names: Dict[str, str]
        labels: Dict[str, int]
        images: Dict[str, ImageEntry]  # key = series UID

    # ----------------------------
    # API Endpoint
    # ----------------------------
    @router.post("/create_task")
    def create_task(data: TaskCreationRequest = Body(...)):
        task_upper = data.task_name.upper()

        # Check for existing task entries in the mapping
        task_entries = [
            (case_id, info) for case_id, info in instance_mapping.items()
            if info.get("task_name", "").lower() == data.task_name.lower()
        ]

        # Task exists: locate existing folder or create new if not found
        if task_entries:
            dataset_folder_name = None
            for case_id, info in task_entries:
                candidate = f"Dataset{int(case_id.split('_')[1]):03d}_{info['task_name']}"
                if (NNUNET_RAW / candidate).exists():
                    dataset_folder_name = candidate
                    break

            if dataset_folder_name:
                raw_folder = NNUNET_RAW / dataset_folder_name
                ensure_extra_folders(dataset_folder_name)
                dataset_folder = raw_folder
                next_counter = max([int(cid.split('_')[1]) for cid, _ in task_entries]) + 1
            else:
                raw_folder, _, _ = ensure_all_task_folders(data.task_name)
                dataset_folder = raw_folder
                next_counter = 1
        else:
            raw_folder, _, _ = ensure_all_task_folders(data.task_name)
            dataset_folder = raw_folder
            next_counter = 1

        num_training_new, num_testing_new = 0, 0

        for series_uid, entry in data.images.items():
            # Skip if already exists
            if any(
                info.get("series_uid") == series_uid and
                info.get("instance_uid") == entry.instance_uid and
                info.get("task_name", "").lower() == data.task_name.lower()
                for info in instance_mapping.values()
            ):
                continue

            # Check if case already exists for this series
            existing_case = next(
                (cid for cid, info in instance_mapping.items()
                if info.get("series_uid") == series_uid and
                    info.get("task_name", "").lower() == data.task_name.lower()),
                None
            )

            if existing_case:
                case_identifier = existing_case
                labels_folder = dataset_folder / "labelsTr"
                for file in labels_folder.glob(f"{case_identifier}*"):
                    try:
                        file.unlink()
                    except Exception:
                        pass
            else:
                case_identifier = f"{task_upper}_{next_counter:03d}"
                next_counter += 1
                for sub in ["imagesTr", "imagesTs", "labelsTr"]:
                    (dataset_folder / sub).mkdir(parents=True, exist_ok=True)

            # Update counters
            if entry.training:
                num_training_new += 1
            else:
                num_testing_new += 1

            # Determine destination paths
            modality_lower = entry.modality.lower()
            channel_code = "0001" if modality_lower == "mri" else "0000"
            new_image_filename = f"{case_identifier}_{channel_code}.nii.gz"
            target_img_folder = dataset_folder / ("imagesTr" if entry.training else "imagesTs")
            img_destination = target_img_folder / new_image_filename

            if not existing_case:
                try:
                    source_img_file = find_image_file(series_uid)
                    shutil.move(str(source_img_file), str(img_destination))
                except FileNotFoundError as e:
                    raise HTTPException(status_code=404, detail=str(e))

            # Handle label (only for training)
            new_label_filename = None
            if entry.training:
                try:
                    source_label_file = find_label_file(entry.instance_uid)
                    new_label_filename = f"{case_identifier}.nii.gz"
                    label_destination = dataset_folder / "labelsTr" / new_label_filename
                    shutil.move(str(source_label_file), str(label_destination))
                except FileNotFoundError as e:
                    raise HTTPException(status_code=404, detail=str(e))

            # Save case to mapping
            instance_mapping[case_identifier] = {
                "task_name": data.task_name,
                "series_uid": series_uid,
                "instance_uid": entry.instance_uid,
                "modality": entry.modality,
                "training": entry.training,
                "image_filename": new_image_filename,
                "label_filename": new_label_filename,
            }

        # Compute overall training/testing counts
        overall_training = sum(
            1 for info in instance_mapping.values()
            if info.get("task_name", "").lower() == data.task_name.lower() and info.get("training")
        )
        overall_testing = sum(
            1 for info in instance_mapping.values()
            if info.get("task_name", "").lower() == data.task_name.lower() and not info.get("training")
        )

        # Save dataset.json
        fixed_labels = {"background": 0}
        fixed_labels.update(data.labels)

        dataset_json = {
            "channel_names": data.channel_names,
            "labels": fixed_labels,
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

        return {
            "message": "Task created/updated successfully.",
            "dataset_folder": dataset_folder.name,
            "num_training": overall_training,
            "num_testing": overall_testing,
            "instance_mapping_update": instance_mapping
        }


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

