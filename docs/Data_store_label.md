# README: Label Reshape

## Step 1: ADD API in Monai Label Server

1. Navigate to the path of your Monai Label installation, typically:

   ```
    C:\Users\"your user name"\AppData\Local\Programs\Python\Python39\Lib\site-packages\monailabel   
    ```

   Replace `Python39` with your Python version.

2. ADD a new API in `datastore.py` in the `endpoints` directory.

3. Add the following code to `datastore.py`:
```python

    from typing import Any, Dict, List, Optional
    from pathlib import Path
    from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

   def zero_pad_label(instance_uid: str, start_index: int, end_index: int, target_shape: tuple):
    label_path = LABEL_DATA_DIR / f"{instance_uid}.nii.gz"
    if not label_path.exists():
        raise FileNotFoundError(f"Label NIfTI file not found: {label_path}")
    
    label_img = nib.load(str(label_path))
    label_data = label_img.get_fdata()
    flipped_label = label_data[:, :, ::-1]  # Reverse along the third dimension
    padded_label = np.zeros(target_shape)
    # Ensure dimensions match
    padded_label[:, :, start_index:end_index+1] = flipped_label[:, :, :end_index-start_index+1]
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
        results[series_uid] = {
        "start_index": int(start_index),
        "end_index": int(end_index),
        "padded_label_shape": tuple(padded_label.shape) 
        }

    return results
   ```
4. Navigate to the path of your Monai Label
```
    C:\Users\"your user name"\AppData\Local\Programs\Python\Python39\Lib\site-packages\monailabel\datastore\dicom.py  
```

   Replace `Python39` with your Python version.

5. Add the following code to `dicom.py`:
```python
def get_label_dir_dcm (self, image_id: str)-> str:
        logger.info(f"Image ID: {image_id}")
        image_dir = os.path.realpath(os.path.join(self._datastore.image_path(), image_id))
        logger.info(f"Image Dir (cache): {image_dir}")

        if not os.path.exists(image_dir) or not os.listdir(image_dir):
            dicom_web_download_series(None, image_id, image_dir, self._client, self._fetch_by_frame)
            
        if not self._convert_to_nifti:
            return image_dir

        image_nii_gz = os.path.realpath(os.path.join(self._datastore.image_path(), f"{image_id}.nii.gz"))
        if not os.path.exists(image_nii_gz):
            image_nii_gz = dicom_to_nifti(image_dir)
            super().add_image(image_id, image_nii_gz, self._dicom_info(image_id))
        return image_dir
   ```
6. Navigate to this path 
```
    C:\Users\"your user name"\AppData\Local\Programs\Python\Python39\Lib\site-packages\monailabel\interfaces\datastore.py
```
7. Add the following code to `datastore.py`:
```python
    def get_label_dir_dcm (self, image_id: str)-> str:
        pass

   ```
