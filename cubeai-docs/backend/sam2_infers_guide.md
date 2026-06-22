# SAM Infers Code Documentation

## Overview
This project implements a 3D medical image segmentation pipeline using the Segment Anything Model (SAM). The pipeline processes NIfTI volumes, converts them into 2D slices in JPEG format, applies 2D segmentation using user-provided prompts (points or boxes), and then reconstructs the segmented results into a 3D volume.

To optimize memory management, the inference state is deleted at the end of a segmentation session but can be restored when re-entering another session for the same volume. This avoids redundant computations while leveraging the accumulative learning of the model during inference.

## Code Structure
The code is organized into three main classes:
1. **ImageProcessor**: Handles image loading, conversion, and processing.
2. **StateManager**: Manages saving and loading inference states.
3. **SAM**: Implements the SAM-based segmentation task.

---

## **Class: ImageProcessor**
### **Description**
This class provides utility methods for loading, processing, and converting medical images (NIfTI format) into a format suitable for segmentation.

### **Methods**
#### 1. `load_nifti(image_path)`
**Purpose**: Loads a NIfTI image from the specified path and returns its data as a NumPy array.

- **Parameters**:
  - `image_path (str)`: Path to the NIfTI file.
- **Returns**:
  - `numpy.ndarray`: A 3D array representing the image data.

#### 2. `convert_volume_to_frames(image_arr, axis, output_dir)`
**Purpose**: Converts a 3D volume into a series of 2D slices along a specified axis and saves them as JPEG images.

- **Parameters**:
  - `image_arr (numpy.ndarray)`: The 3D image array.
  - `axis (int)`: The axis along which to extract slices (0: sagittal, 1: coronal, 2: axial).
  - `output_dir (str)`: Directory to save the JPEG slices.
- **Behavior**:
  - Normalizes each slice to the range `[0, 255]`.
  - Saves each slice as a JPEG image in the specified directory.

#### 3. `reconstruct_nifti(video_segments)`
**Purpose**: Reconstructs a 3D segmentation mask from a dictionary of 2D segmentation results.

- **Parameters**:
  - `video_segments (dict)`: A dictionary where keys are frame indices and values are dictionaries of object IDs and their corresponding masks.
- **Returns**:
  - `numpy.ndarray`: A 3D array representing the reconstructed segmentation mask.

#### 4. `resize_to_match(target_shape, source)`
**Purpose**: Resizes a source 3D array to match the target shape.

- **Parameters**:
  - `target_shape (tuple)`: The desired shape of the output array.
  - `source (numpy.ndarray)`: The source 3D array to resize.
- **Returns**:
  - `numpy.ndarray`: The resized array.
- **Behavior**:
  - Handles cases where the source and target shapes differ along any of the three dimensions.
  - Crops or pads the source array as needed.
  - Takes into consideration the reversion of the slice numbering between OHIF and MONAI.

---

## **Class: StateManager**
### **Description**
This class manages the saving and loading of inference states, which include intermediate results and model states required for segmentation. The inference state is deleted to manage memory effectively but can be restored to retain learned information from previous runs.

### **Methods**
#### 1. `save_state(state)`
**Purpose**: Saves the inference state to a temporary file.

- **Parameters**:
  - `state (dict)`: The inference state to save.
- **Returns**:
  - `str`: Path to the saved state file.

#### 2. `load_state(file_path)`
**Purpose**: Loads the inference state from a file.

- **Parameters**:
  - `file_path (str)`: Path to the state file.
- **Returns**:
  - `dict`: The loaded inference state.

---

## **Class: SAM**
### **Description**
This class implements the SAM-based segmentation task. It handles the initialization of the SAM predictor, processes user prompts, and performs 2D and 3D segmentation on medical images.

### **Key Parameters**
- `self.predictor`: The SAM predictor for segmentation.
- `self.annotated_image`: The 3D array storing the segmentation results.
- `self.inference_state`: The current state of the inference process.
- `self.jpeg_filepath`: Directory to store JPEG slices.
- `self.checkpoint_path`: Path to the SAM model checkpoint.
- `self.config_path`: Path to the SAM configuration file.

### **Methods**
#### 1. `initialize_predictor(device="cuda")`
**Purpose**: Initializes the SAM predictor if not already initialized.

- **Parameters**:
  - `device (str)`: Device to use for inference (default: "cuda").

#### 2. `run_inferer(data, device="cuda")`
**Purpose**: Runs the inference pipeline on the input data.

- **Parameters**:
  - `data (dict)`: Input data containing the image path and segmentation state.
  - `device (str)`: Device to use for inference (default: "cuda").
- **Behavior**:
  - Loads the 3D NIfTI image.
  - Converts the volume to 2D slices if processing a new volume.
  - Performs 2D or 3D segmentation based on the input state.
  - Saves the inference state and cleans up memory.

#### 3. `segment_objects(data, image_array)`
**Purpose**: Processes user prompts (points or boxes) and performs 2D segmentation on the specified slice.

- **Parameters**:
  - `data (dict)`: Input data containing prompts and slice index.
  - `image_array (numpy.ndarray)`: The 3D image array.
- **Behavior**:
  - Extracts points and boxes from the input data.
  - Filters prompts for the specified slice.
  - Calls `segment_2d` to perform segmentation.

#### 4. `segment_2d(frame_idx, obj_id, points, labels, box)`
**Purpose**: Performs 2D segmentation on a specific slice using the SAM predictor.

- **Parameters**:
  - `frame_idx (int)`: Index of the slice to process.
  - `obj_id (str)`: ID of the object to segment.
  - `points (numpy.ndarray)`: Array of points for segmentation.
  - `labels (numpy.ndarray)`: Labels for the points (1: positive, 0: negative).
  - `box (numpy.ndarray)`: Bounding box for segmentation.
- **Behavior**:
  - Adds points or boxes to the SAM predictor (SAM function).
  - Updates the annotated image with the new segmentation mask.

---

## **Usage**
1. Load a NIfTI image using `ImageProcessor.load_nifti()`.
2. Convert the image into 2D slices with `convert_volume_to_frames()`.
3. Initialize the `SAM` class and run the inference pipeline with `run_inferer()`.
4. Segment objects using `segment_objects()` with user-defined prompts.
5. Reconstruct the segmented slices into a 3D volume using `reconstruct_nifti()`.

---

## **License**
This project is licensed under the MIT License. Feel free to modify and distribute it as needed.

