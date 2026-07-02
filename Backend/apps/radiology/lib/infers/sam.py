# # # Copyright (c) MONAI Consortium
# # # Licensed under the Apache License, Version 2.0 (the "License");
# # # you may not use this file except in compliance with the License.
# # # You may obtain a copy of the License at
# # #     http://www.apache.org/licenses/LICENSE-2.0
# # # Unless required by applicable law or agreed to in writing, software
# # # distributed under the License is distributed on an "AS IS" BASIS,
# # # WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# # # See the License for the specific language governing permissions and
# # # limitations under the License.

import torch
import logging
import os
import nibabel as nib
import numpy as np
from enum import Enum
from typing import Callable, Sequence
from PIL import Image
import shutil
import gc
import time
import tempfile
import pickle
import os


from monai.inferers import Inferer, SlidingWindowInferer, SliceInferer
from monailabel.interfaces.tasks.infer_v2 import InferType
from monailabel.tasks.infer.basic_infer import BasicInferTask

from sam2.build_sam import build_sam2_video_predictor

logger = logging.getLogger(__name__)


class CallBackTypes(str, Enum):
    PRE_TRANSFORMS = "PRE_TRANSFORMS"
    INFERER = "INFERER"
    INVERT_TRANSFORMS = "INVERT_TRANSFORMS"
    POST_TRANSFORMS = "POST_TRANSFORMS"
    WRITER = "WRITER"


class ImageProcessor:
    """Handles image loading, conversion, and processing."""

    @staticmethod
    def load_nifti(image_path):
        nii_image = nib.load(image_path)
        array = nii_image.get_fdata()
        spacing = nii_image.header.get_zooms()  # (z, y, x)
        origin = nii_image.affine[:3, 3]  # physical origin

        return array, spacing, origin

    @staticmethod
    def convert_volume_to_frames(image_arr, axis, output_dir):
        if os.path.exists(output_dir):

            # Delete all files and subdirectories in the base output directory
            shutil.rmtree(output_dir)

        # Create the base output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)

        num_slices = image_arr.shape[axis]
        for slice_idx in range(num_slices):
            # Extract the slice based on the axis
            slice_data = image_arr.take(indices=slice_idx, axis=axis)
            slice_normalized = ((slice_data - np.min(slice_data)) / (np.max(slice_data) - np.min(slice_data)) * 255).astype(np.uint8)

            # Convert to PIL Image and save as JPEG
            Image.fromarray(slice_normalized).save(os.path.join(output_dir, f"{slice_idx:05}.jpg"))
        logger.info(f"Saved {num_slices} slices to {output_dir}.")

    @staticmethod
    def reconstruct_nifti(video_segments, binarize=False):
        # Filter out empty slices
        video_segments = {k: v for k, v in video_segments.items() if v}
        if not video_segments:
            raise ValueError("No valid masks found in video_segments")

        slice_indices = sorted(video_segments.keys())
        max_index = max(slice_indices)
        logger.info(f"Max num of slice is {max_index}")

        # Get shape from first valid mask
        for idx in slice_indices:
            if video_segments[idx]:
                first_mask = list(video_segments[idx].values())[0]
                break

        if first_mask.ndim == 3:   # shape (1, H, W)
            H, W = first_mask.shape[1], first_mask.shape[2]
        elif first_mask.ndim == 2: # shape (H, W)
            H, W = first_mask.shape
        else:
            raise ValueError(f"Unexpected mask shape: {first_mask.shape}")

        mask_volume = np.zeros((H, W, max_index + 1), dtype=np.uint8)

        # Fill volume slice by slice
        for slice_idx, obj_masks in video_segments.items():
            for obj_id, mask in obj_masks.items():
                if binarize:
                    # All objects become binary 1
                    mask_volume[:, :, slice_idx] = np.where(
                        mask > 0,
                        1,
                        mask_volume[:, :, slice_idx]
                    )
                else:
                    # Each object keeps unique ID (multi-label)
                    mask_volume[:, :, slice_idx] = np.where(
                        mask > 0,
                        obj_id,
                        mask_volume[:, :, slice_idx]
                    )

        return mask_volume

    @staticmethod
    def resize_to_match(target_shape, source):
        result = np.zeros(target_shape, dtype=source.dtype)
        min_shape = tuple(min(t, s) for t, s in zip(target_shape, source.shape))

        # Copy overlapping region
        result[:min_shape[0], :min_shape[1], :min_shape[2]] = source[:min_shape[0], :min_shape[1], :min_shape[2]]

        if target_shape != source.shape:
            logger.info(f"Warning: Shape mismatch fixed. Target={target_shape}, Source={source.shape}")

        return result

    @staticmethod
    def load_nrrd_file(image_path):
        """Load NRRD files using pynrrd library"""
        try:
            import nrrd
            array, header = nrrd.read(image_path)
            
            spacing = header.get('space directions', None)
            if spacing is not None:
                spacing = [abs(spacing[i][i]) for i in range(len(spacing))]
            else:
                spacing = header.get('spacings', [1.0, 1.0, 1.0])
            
            # Extract origin
            origin = header.get('space origin', [0.0, 0.0, 0.0])
            
            return array, spacing, origin
            
        except ImportError:
            raise ImportError("pynrrd library required for NRRD files. Install with: pip install pynrrd")
    
    @staticmethod
    def detect_file_format(data_bytes: bytes):
        """Detect if file is NRRD or NIfTI based on header"""
        if data_bytes.startswith(b'NRRD'):
            return 'nrrd'
        elif data_bytes[344:348] == b'n+1\x00' or data_bytes[344:348] == b'ni1\x00':
            return 'nifti'
        else:
            try:
                header_text = data_bytes[:1000].decode('ascii', errors='ignore')
                if 'NRRD' in header_text or 'dimension:' in header_text:
                    return 'nrrd'
            except:
                pass
            return 'unknown'
    
    @staticmethod
    def load_via_tempfile(data_bytes: bytes):
        """
        Load image data from bytes using appropriate method based on format
        
        Args:
            data_bytes: Raw bytes of image file
            
        Returns:
            tuple: (array, spacing, origin)
        """
        try:
            # Detect file format
            file_format = ImageProcessor.detect_file_format(data_bytes)
            
            if file_format == 'nrrd':
                suffix = '.nrrd'
                load_func = ImageProcessor.load_nrrd_file
            elif file_format == 'nifti':
                suffix = '.nii'
                load_func = ImageProcessor.load_nifti
            else:
                # Default to NRRD since your data starts with NRRD header
                suffix = '.nrrd'
                load_func = ImageProcessor.load_nrrd_file
            
            # Create temporary file with appropriate suffix
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
                temp_file.write(data_bytes)
                temp_path = temp_file.name
            
            try:
                # Load using appropriate function
                array, spacing, origin = load_func(temp_path)
                return array, spacing, origin
            finally:
                # Clean up temporary file
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                    
        except Exception as e:
            raise ValueError(f"Could not load image data via temporary file: {e}")
    
    @staticmethod
    def load_nrrd_from_bytes(data_bytes: bytes):
        """
        Direct parsing of NRRD data from bytes without temporary file
        
        Args:
            data_bytes: Raw bytes of NRRD file
            
        Returns:
            tuple: (array, spacing, origin, metadata)
        """
 
        header_end = data_bytes.find(b'\n\n')
        if header_end == -1:
            header_end = data_bytes.find(b'\r\n\r\n')
        if header_end == -1:
            raise ValueError("Could not find end of NRRD header")
        
     
        header_bytes = data_bytes[:header_end]
        header_text = header_bytes.decode('ascii', errors='ignore')
        
  
        header_info = {}
        lines = header_text.split('\n')
        
        for line in lines:
            line = line.strip()
            if line.startswith('#') or not line or ':' not in line:
                continue
                
            key, value = line.split(':', 1)
            key = key.strip().lower()
            value = value.strip()
            
            if key == 'dimension':
                header_info['dimension'] = int(value)
            elif key == 'type':
                header_info['type'] = value
            elif key == 'sizes':
                header_info['sizes'] = [int(x) for x in value.split()]
            elif key == 'encoding':
                header_info['encoding'] = value
            elif key == 'space directions':
         
                directions = value.replace('(', '').replace(')', '').split()
                header_info['space_directions'] = directions
            elif key == 'space origin':
                origin_str = value.replace('(', '').replace(')', '')
                header_info['space_origin'] = [float(x) for x in origin_str.split(',')]
        

        data_start = header_end + 2
        binary_data = data_bytes[data_start:]
        
        # Convert to numpy array
        dtype_map = {
            'uint8': np.uint8, 'int8': np.int8,
            'uint16': np.uint16, 'int16': np.int16, 
            'uint32': np.uint32, 'int32': np.int32,
            'float': np.float32, 'double': np.float64
        }
        
        numpy_dtype = dtype_map.get(header_info.get('type', 'uint8'), np.uint8)
        flat_array = np.frombuffer(binary_data, dtype=numpy_dtype)
        

        dimensions = header_info.get('sizes', [])
        if dimensions:
            try:
                array = flat_array.reshape(dimensions)
            except ValueError:
                logger.info(f"Warning: Could not reshape to {dimensions}")
                array = flat_array
        else:
            array = flat_array
        
        spacing = [1.0, 1.0, 1.0]  # default
        origin = [0.0, 0.0, 0.0]   # default
        
        if 'space_origin' in header_info:
            origin = header_info['space_origin']
        
        
        return array, spacing, origin, header_info



class StateManager:
    """Manages saving and loading inference states."""

    @staticmethod
    def save_state(state):
         # Get the temporary directory
        temp_file_path = os.path.join(tempfile.gettempdir(), "inference_state.pkl")
        with open(temp_file_path, "wb") as temp_file:
             # Serialize and save the state
            pickle.dump(state, temp_file)
        return temp_file_path

    @staticmethod
    def load_state(file_path):
        if os.path.exists(file_path):
            with open(file_path, "rb") as temp_file:
                # Deserialize and load the state
                return pickle.load(temp_file)
        raise FileNotFoundError("Inference state file not found!")


class sam(BasicInferTask):
    """Implements SAM-based segmentation with improved structure."""

    def __init__(
            self,
            path,
            network=None,
            type=InferType.SEGMENTATION,
            labels=None,
            dimension=3,
            description=" Pretrained SAM Model for video segmentation ",
            **kwargs,
        ):
            super().__init__(
                path=path,
                network=network,
                type=type,
                labels=labels,
                dimension=dimension,
                description=description,
                load_strict=True,
                **kwargs,
            )
            self.annotated_image = None
            self.image_path = None
            self.inference_state = None
            self.jpeg_filepath = "./jpeg_slice_files"
            # self.mask_data = None 
            self.current_volume_id = None   # track which volume is active

            self.config_path = "configs/sam2.1/sam2.1_hiera_l"
            self.hydra_overrides_extra = [
                "++model._target_=custom_sam.sam2_volume_predictor.SAM2VolumePredictor",
            ]
            self.predictor = None

            self.object_bounds = {}  # Dictionary to store bounds per object

    def initialize_predictor(self, device="cuda"):
        if self.predictor is None:
            self.predictor = build_sam2_video_predictor(self.config_path, self.path[0], hydra_overrides_extra=self.hydra_overrides_extra, device=device)

    def run_inferer(self, data, device="cuda"):
        """
        Run Inferer over pre-processed Data.
        """
        start_time = time.time()

        try:
            # Load the 3D NIfTI image
            volume_id = data["image"]
            image_array, spacing, origin  = ImageProcessor.load_nifti(data["image"])

            # Initialize the predictor for new task
            self.initialize_predictor(device=device)
            
            if not hasattr(self, "current_volume_id") or self.current_volume_id != volume_id:
                logger.info(f"Initializing NEW volume: {volume_id}")
                self.current_volume_id = volume_id
                self._initialize_new_volume(data, image_array)
            else:
                logger.info(f"Reusing existing volume state: {volume_id}")
                # load cached state if available
                if hasattr(self, "temp_state_path") and self.temp_state_path:
                    self.inference_state = StateManager.load_state(self.temp_state_path)
                    self.annotated_image = np.zeros_like(image_array, dtype=np.int32)
                    self.object_bounds = {}                    


            if data["State"] == "2d":
                self._handle_2d(data, image_array)

            # elif data["State"] == "Mask":
            #     self.mask_data = data.get("Mask")
            #     self._handle_mask(self.mask_data)

            elif data["State"] == "Auxillary_Box":
                self._handle_auxillary_box(data, image_array, device)

            elif data["State"] == "3d":
                self._handle_3d(data, image_array)

            else:
                raise ValueError(f"Unknown state")

            # save results
            data["pred"] = self.annotated_image
            self.temp_state_path = StateManager.save_state(self.inference_state)

        except Exception as e:
            self.cleanup_inference_state()
            raise
        finally:
            self.cleanup_inference_state()
            logger.info(f"Execution time: {time.time() - start_time:.2f} seconds")

        return data
    
    def _initialize_new_volume(self, data, image_array):
       
        """Initialize inference for a new volume."""
        self.image_path = data["image"]
        self.inference_state = self.predictor.init_state(video_path=image_array)
        self.annotated_image = np.zeros_like(image_array, dtype=np.int32)
        self.StartSliceIndex = image_array.shape[2]
        self.object_bounds = {}
        logger.info("New volume initialized.")
    
    def _handle_2d(self, data, image_array):
        """handle 2D segmentation."""
        self.predictor.reset_state(self.inference_state)
        if data.get("Mask") is not None :
            self._handle_mask(data.get("Mask"))
        self.segment_objects(data, image_array)
    
    def _handle_auxillary_box(self, data, image_array, device):
        """handle Auxillary_Box segmentation."""
        #Extract the box data
        box_arr = data['Auxillary_Box'][0]
        slice_index=data['Auxillary_Box'][1]
        orientation_axis =data['Auxillary_Box'][2]
        obj_id=data["Auxillary_Id"]
        logger.info(f"obj_id: {obj_id}")

        logger.info(f"box_arr: {box_arr}")
        logger.info(f"slice_index: {slice_index}")
        logger.info(f"orientation_axis: {orientation_axis}")

        #Extract slice data
        slice_data = image_array[slice_index,:,:]
        slice_normalized = ((slice_data - np.min(slice_data)) / (np.max(slice_data) - np.min(slice_data)) * 255).astype(np.uint8)

        logger.info(f"image_array shape: {image_array.shape}")
        logger.info(f"Shape of slice: {slice_data.shape}")
        #Remove directory if it exists
        if os.path.exists(self.jpeg_filepath):
            try:
                shutil.rmtree(self.jpeg_filepath)
                logger.info(f"Removed existing directory: {self.jpeg_filepath}")
            except Exception as e:
                logger.info(f"Error removing directory: {e}")
                raise

        #Create fresh directory
        try:
            os.makedirs(self.jpeg_filepath, exist_ok=True)
            logger.info(f"Created clean directory: {self.jpeg_filepath}")
        except Exception as e:
            logger.info(f"Error creating directory: {e}")
            raise

        # Convert to PIL Image and save as JPEG
        Image.fromarray(slice_normalized).save(os.path.join(self.jpeg_filepath, f"{slice_index:05}.jpg"))

        #initialize a new predictor and new inference with only one slice .
        auxillary_predictor = build_sam2_video_predictor(self.config_path, self.path[0], device=device)

        auxillary_inference_state = auxillary_predictor.init_state(video_path=self.jpeg_filepath)

        # Add the point/box to the predictor
        _,out_obj_ids, out_mask_logits = auxillary_predictor.add_new_points_or_box(
            inference_state=auxillary_inference_state,
            frame_idx=int(0),
            obj_id=obj_id,
            points=[],
            labels=[],
            box=[box_arr] if len([box_arr]) > 0 else None
        )
        logger.info(f"out_mask_logits: {out_mask_logits}")
        logger.info(f"obj_id: {out_obj_ids}")

        # Convert tensor to NumPy array where self.out_mask_logits=[obj_id][chanel:0 for grayscale]
        obj_id_int = int(obj_id)

        mask_array =out_mask_logits[0][0].cpu().numpy()

        logger.info(f"mask_array shape: {mask_array.shape}")

        if obj_id not in self.object_bounds:
            self.object_bounds[obj_id] = [None, None]

        logger.info(f"obj_id_int: {obj_id_int}")
        logger.info(f"self.object_bounds: {self.object_bounds}")

        # Calculate width metrics (column indices where mask > 0)
        nonzero_cols = np.where(np.any(mask_array > 0, axis=0))[0]  # Get columns with at least one positive pixel

        if len(nonzero_cols) > 0:
            min_col = np.min(nonzero_cols)
            max_col = np.max(nonzero_cols)
            logger.info(f"Segmentation spans columns: {min_col} to {max_col}")
                        
        if self.object_bounds[obj_id][0] is None or min_col < self.object_bounds[obj_id][0]:
            self.object_bounds[obj_id][0] = min_col
        if self.object_bounds[obj_id][1] is None or max_col > self.object_bounds[obj_id][1]:
            self.object_bounds[obj_id][1] = max_col
            
        if(orientation_axis==0):

            reversed_slice_index=slice_index #No reversion in coronal

            #Clearing first
            self.annotated_image[int(reversed_slice_index), :, :] = np.where(
                self.annotated_image[int(reversed_slice_index), :, :] == int(obj_id),
                0,
                self.annotated_image[int(reversed_slice_index), :, :]
            )

            # Add the new segmentation mask
            self.annotated_image[int(reversed_slice_index), :, :] = np.where(
                mask_array > 0,
                obj_id,
                self.annotated_image[int(reversed_slice_index), :, :]
            )
        else:

            slice_index =image_array.shape[1] - int(slice_index) -1 #reverse the slice first

            #Clearing
            self.annotated_image[:, int(slice_index), :] = np.where(
                self.annotated_image[:, int(slice_index), :] == int(obj_id),
                0,
                self.annotated_image[:, int(slice_index), :]
            )

            # Add the new segmentation mask
            self.annotated_image[:, int(slice_index), :] = np.where(
                mask_array > 0,
                obj_id,
                self.annotated_image[:, int(slice_index), :]
            )


        logger.info(f"Current object bounds (width): {self.object_bounds[obj_id]}")
             
    def _handle_mask(self, mask_data):
        """Handle mask refinement state."""
       
        mask = mask_data
        if not mask or "0" not in mask:
            raise ValueError("Expected indexed mask data not found")

        max_index = max(int(k) for k in mask.keys() if k.isdigit())
        mask_list = [mask[str(i)] for i in range(max_index + 1)]
        mask_bytes = bytes(mask_list)
        mask_array_input, _, _ = ImageProcessor.load_via_tempfile(mask_bytes)

        unique_labels = np.unique(mask_array_input)
        unique_labels = unique_labels[unique_labels != 0] # to remove background
        # loop through each label through all slices
        for idx, label in enumerate(unique_labels, start=1):
            for slice_idx in range(mask_array_input.shape[2]):
                _, out_obj_ids, out_mask_logits = self.predictor.add_new_mask(
                    inference_state=self.inference_state,
                    frame_idx=slice_idx,
                    obj_id=idx,
                    mask=mask_array_input[:, :, slice_idx],
                )

                mask_array_output = out_mask_logits[0][0].cpu().numpy()

                self.annotated_image[:, :, slice_idx] = np.where(
                    self.annotated_image[:, :, slice_idx] == idx,
                    0,
                    self.annotated_image[:, :, slice_idx],
                )
                self.annotated_image[:, :, slice_idx] = np.where(
                    mask_array_output > 0,
                    idx,
                    self.annotated_image[:, :, slice_idx],
                )
   
    def _handle_3d(self, data, image_array):
        """handle 3D Propagation"""
        logger.info(f"self.StartSliceIndex in 3d: {self.StartSliceIndex}")
        self.StartSliceIndex = image_array.shape[2] - int(self.StartSliceIndex) - 1
        logger.info(f"self.StartSliceIndex after inversion: {self.StartSliceIndex}")


        video_segments = {}

        # margin
        total_slices = image_array.shape[2]
        margin = int(0.05 * total_slices)  # 5% of total slices as margin

        
        if not self.object_bounds:
            logger.info("no bounding box defined, defaulting to forward & Backward propagation for all slices")
            for out_frame_idx, out_obj_ids, out_mask_logits in self.predictor.propagate_in_video(
                self.inference_state,
                start_frame_idx=int(self.StartSliceIndex),
                reverse=True  # forward
            ):
                video_segments[out_frame_idx] = {
                    out_obj_id: (out_mask_logits[i] > 0.0).cpu().numpy()
                    for i, out_obj_id in enumerate(out_obj_ids)
                }
            
            for out_frame_idx, out_obj_ids, out_mask_logits in self.predictor.propagate_in_video(
                self.inference_state,
                start_frame_idx=int(self.StartSliceIndex)-1,
                reverse=False  # Backward
            ):
                video_segments[out_frame_idx] = {
                    out_obj_id: (out_mask_logits[i] > 0.0).cpu().numpy()
                    for i, out_obj_id in enumerate(out_obj_ids)
                }           
                
        else:
            # iteration over boundlimits
            for obj_id, bounds in self.object_bounds.items():
                if bounds[0] is None or bounds[1] is None:
                    logger.info(f"Object {obj_id} has undefined bounds, defaulting to forward & Backward propagation")
                    for out_frame_idx, out_obj_ids, out_mask_logits in self.predictor.propagate_in_video(
                        self.inference_state,
                        start_frame_idx=int(self.StartSliceIndex),
                        reverse=True  # forward
                    ):
                        video_segments[out_frame_idx] = {
                            out_obj_id: (out_mask_logits[i] > 0.0).cpu().numpy()
                            for i, out_obj_id in enumerate(out_obj_ids)
                            if out_obj_id == obj_id  
                        }
                        
                    for out_frame_idx, out_obj_ids, out_mask_logits in self.predictor.propagate_in_video(
                        self.inference_state,
                        start_frame_idx=int(self.StartSliceIndex)-1,
                        reverse=False  # backward
                    ):
                        video_segments[out_frame_idx] = {
                            out_obj_id: (out_mask_logits[i] > 0.0).cpu().numpy()
                            for i, out_obj_id in enumerate(out_obj_ids)
                            if out_obj_id == obj_id  
                        }                        
                else:
                    # calculate bounds with margin
                    lower_bound = max(0, bounds[0] - margin)
                    upper_bound = min(total_slices - 1, bounds[1] + margin)
                    logger.info(f"Object {obj_id} - Propagating from slice {lower_bound} to {upper_bound}")

                    # forward to upper limits
                    if self.StartSliceIndex <= upper_bound:
                        for out_frame_idx, out_obj_ids, out_mask_logits in self.predictor.propagate_in_video(
                            self.inference_state,
                            start_frame_idx=int(self.StartSliceIndex),
                            reverse=True,  
                            max_frame_num_to_track=upper_bound + 1 
                        ):
                            if lower_bound <= out_frame_idx <= upper_bound:
                                video_segments[out_frame_idx] = {
                                    out_obj_id: (out_mask_logits[i] > 0.0).cpu().numpy()
                                    for i, out_obj_id in enumerate(out_obj_ids)

                                }
                            else :
                                break

                    # backward to lower
                    if self.StartSliceIndex - 1 >= lower_bound:
                        for out_frame_idx, out_obj_ids, out_mask_logits in self.predictor.propagate_in_video(
                            self.inference_state,
                            start_frame_idx=int(self.StartSliceIndex) - 1,
                            reverse=False,  
                            max_frame_num_to_track=lower_bound  
                        ):
                            if lower_bound <= out_frame_idx <= upper_bound:
                                video_segments[out_frame_idx] = {
                                    out_obj_id: (out_mask_logits[i] > 0.0).cpu().numpy()
                                    for i, out_obj_id in enumerate(out_obj_ids)
                        
                                }
                            else:
                                break

        # clear old segmentation
        self.annotated_image = np.zeros_like(image_array, dtype=np.int32)

        # reconstruct the JPEG slices into volume mask
        if video_segments:
            logger.info("the video segments is {video_segments}")
            mask = ImageProcessor.reconstruct_nifti(video_segments)
            # resize the mask to fit in the annotated image
            self.annotated_image = ImageProcessor.resize_to_match(self.annotated_image.shape, mask)
        else:
            logger.info("No valid segments generated")

        
    
    def cleanup_inference_state(self):
        """Helper method to clean up inference state safely"""
        if hasattr(self, 'inference_state'):
            try:
                for key in self.inference_state:
                    if isinstance(self.inference_state[key], dict):
                        for subkey in self.inference_state[key]:
                            tensor = self.inference_state[key][subkey]
                            if isinstance(tensor, torch.Tensor):
                                self.inference_state[key][subkey] = tensor.cpu()
                del self.inference_state
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception as e:
                logger.error(f"Error during cleanup: {str(e)}")

    def segment_objects(self, data,image_array):
        frame_idx = data["SliceIndex"]  # Get the specific slice index to process
        labels =None                    # Labels of the points: 1->positive point , 0->negative points

        #loop over all objects for segmentation
        for obj_id, prompt_details in data["sam2config"].items():

            logger.info(f"at obj_id: {obj_id} corresponds: {prompt_details}")
            points = prompt_details.get("points", [[], []])     #extract points
            boxes = prompt_details.get("boxes", [])             #extract boxes

            if (len(points) > 0 and len(points[0]) > 0) or (len(boxes) > 0 and len(boxes[0]) > 0):

                    # Process points based on frame index
                    points_with_frames = np.array(points[0], dtype=np.float32) if len(points[0]) > 0 else np.empty((0, 4))
                    labels = np.array(points[1], dtype=np.int32) if len(points[1]) > 0 else np.array([])  # Extract labels

                    points_within_triggered_slice = []
                    Labels_within_triggered_slice = []

                    for point,label in zip(points_with_frames,labels):
                        if point[2] == frame_idx:
                            points_within_triggered_slice.append( [point[0],point[1]] )
                            Labels_within_triggered_slice.append(label)

                    logger.info(f"points_with_frames: {points_with_frames}")
                    logger.info(f"labels: {labels}")
                    logger.info(f"points_within_triggered_slice: {points_within_triggered_slice}")
                    logger.info(f"Labels_within_triggered_slice: {Labels_within_triggered_slice}")


                    boxes_within_triggered_slice=[]
                    if len(boxes) > 0:
                        for box in boxes[0]:
                            if box[1] == frame_idx:
                                boxes_within_triggered_slice.append(box[0])

                    logger.info(f"boxes_within_triggered_slice: {boxes_within_triggered_slice}")

                    if ( (len(boxes_within_triggered_slice) > 0) or (len(Labels_within_triggered_slice) >0) ):

                        # Reverse the slice index as the slice numbering in OHIF is reversed
                        reversed_frame_idx= image_array.shape[2] - frame_idx - 1
                        #Apply 2d segmentation on that slice
                        self.segment_2d(reversed_frame_idx,obj_id,points_within_triggered_slice,Labels_within_triggered_slice,boxes_within_triggered_slice)

                        #keep track of the min refrenced slice index for 3d segmentation
                        logger.info(f"frame_idx: {frame_idx}")
                        logger.info(f"self.StartSliceIndex: {self.StartSliceIndex}")
                        if frame_idx < self.StartSliceIndex:
                            self.StartSliceIndex = frame_idx
                        logger.info(f"self.StartSliceIndex inside the loop: {self.StartSliceIndex}")


    def segment_2d(self,frame_idx,obj_id,points,labels,box):

         # Add the point/box to the predictor
        _, self.out_obj_ids, self.out_mask_logits = self.predictor.add_new_points_or_box(
            inference_state=self.inference_state,
            frame_idx=int(frame_idx),
            obj_id=obj_id,
            points=points,
            labels=labels,
            box=box if len(box) > 0 else None
        )
        logger.info(f"out_mask_logits: {self.out_mask_logits}")
        logger.info(f"obj_id: {self.out_obj_ids}")

        # Convert tensor to NumPy array where self.out_mask_logits=[obj_id][chanel:0 for grayscale]
        obj_id_int = int(obj_id)

        while obj_id_int > 0:
            try:
                mask_array = self.out_mask_logits[obj_id_int - 1][0].cpu().numpy()
                break  # Exit the loop if successful
            except:
                obj_id_int -= 1  # Decrement obj_id and try again

        # Clear the old segmentation
        logger.info(f"Before clearing, slice {frame_idx} unique values: {np.unique(self.annotated_image[:, :, int(frame_idx)])}")

        self.annotated_image[:, :, int(frame_idx)] = np.where(
            self.annotated_image[:, :, int(frame_idx)] == int(obj_id),
            0,
            self.annotated_image[:, :, int(frame_idx)]
        )

        logger.info(f"After clearing, slice {frame_idx} unique values: {np.unique(self.annotated_image[:, :, int(frame_idx)])}")

        # Add the new segmentation mask
        self.annotated_image[:, :, int(frame_idx)] = np.where(
            mask_array > 0,
            obj_id,
            self.annotated_image[:, :, int(frame_idx)]
        )

        logger.info(f"After adding new mask, slice {frame_idx} unique values: {np.unique(self.annotated_image[:, :, int(frame_idx)])}")



    def inferer(self, data=None) -> SlidingWindowInferer:
        return SlidingWindowInferer(roi_size=[128, 128, 32], sw_batch_size=6, overlap=0.1)

    def pre_transforms(self, data=None) -> Sequence[Callable]:
        return []

    def post_transforms(self, data=None) -> Sequence[Callable]:
        return []
