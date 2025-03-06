# README: Data Transfering from PACS SERVER INTO WORKFLOW SERVER

## Step 1: ADD API in Monai Label Server

1. Navigate to the path of your Monai Label installation, typically:

   ```
    C:\Users\"your user name"\AppData\Local\Programs\Python\Python39\Lib\site-packages\monailabel   ```

   Replace `Python39` with your Python version.

2. ADD a new API in `datastore.py` in the `endpoints` directory.

3. Add the following code to `datastore.py`:
```python

    from typing import Any, Dict, List, Optional
    from pathlib import Path
    from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

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
            
            # Update the instance mapping with this new download
            instance_mapping[series_uid] = {
                "series_uid": series_uid,
                "task_name": task_name,
                "file_path": str(target_file)
            }
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

   ```
## Form of the Request Body#

**For Download labels api**

1. Task name : "Brain"

2. **Request Body**
```json
    [
  "Instance_uid_1",
  "Instance_uid_2",
    ]
   ```

**For Download Images api**
1. Task name : "Brain" 

2. **Request Body**
```json
    [
  "Series_uid_1",
  "Series_uid_2",
    ]
   ```
