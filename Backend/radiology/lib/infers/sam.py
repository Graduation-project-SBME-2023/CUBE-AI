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
from typing import  Callable, Sequence
from PIL import Image
import matplotlib.pyplot as plt
import matplotlib.pyplot as plt
import shutil
import gc
import time
import tempfile
import pickle
import os

from monai.inferers import Inferer, SlidingWindowInferer, SliceInferer
from monailabel.interfaces.tasks.infer_v2 import InferType
from monailabel.tasks.infer.basic_infer import BasicInferTask

import sam2
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
    def reconstruct_nifti(video_segments):
        
        # Determine the number of slices from the video_segments dictionary
        num_slices = len(video_segments)
        
        # Retrieve the shape of the first slice to define the full volume dimensions
        first_frame_index = min(video_segments.keys())
        first_mask = list(video_segments[first_frame_index].values())[0]  # Get first available mask
        slice_shape = (first_mask.shape[1], first_mask.shape[2])
        mask_volume = np.zeros((*slice_shape, num_slices), dtype=np.uint8)
        

        # Assign unique values for each obj_id across all slices
        for slice_idx, obj_masks in video_segments.items():
            for obj_id, mask in obj_masks.items():
                # Assign the obj_id as the mask value in the 3D volume
                mask_volume[:, :, slice_idx] = np.where(mask > 0, obj_id, mask_volume[:, :, slice_idx])
    
        return mask_volume

    @staticmethod
    def resize_to_match(target_shape, source):
        result = np.zeros(target_shape, dtype=source.dtype)
        changed_direction=None
        
        for direction in range(3):
            if target_shape[direction] != source.shape[direction]:
                changed_direction=direction
        
        if changed_direction != None:
            if changed_direction == 0:
                source_reversed = source[::-1, :, :] # to be compitable with the other revesion in slices 
                start = source_reversed.shape[0] -1
                end = start + source_reversed.shape[0] 
                if end > result.shape[0]:
                    print("Warning: End index exceeds result boundaries. Cropping source_reversed.")
                    # Calculate the valid range within result
                    valid_length = result.shape[0] - start
                    source_reversed = source_reversed[:valid_length, :, :]  # Crop source_reversed
                    end = result.shape[0]  # Adjust end to match result's limit
                result[start:end,:,:]=source_reversed    
            elif changed_direction == 1:
                end = source.shape[1]
                result[:,0:end,:]=source
                
            elif changed_direction == 2:
                end = source.shape[2]
                result[:,:,0:end]=source

        return result
    
  
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
            self.predictor = None
            self.annotated_image = None
            self.image_path = None
            self.inference_state = None
            self.jpeg_filepath = "./jpeg_slice_files"
            self.checkpoint_path = "C:/SAM2_GP/sam2/checkpoints/sam2.1_hiera_large.pt"
            self.config_path = "F:/uni/GP/deployment5/radiology/sam2/configs/sam2.1/sam2.1_hiera_l"
            self.object_bounds = {}  # Dictionary to store bounds per object
            # self.prev_first_obj_prompts = 0  # Track the number of prompts for the first object

    def initialize_predictor(self, device="cuda"):
        if self.predictor is None:
            self.predictor = build_sam2_video_predictor(self.config_path, self.checkpoint_path, device=device)
       

    def run_inferer(self, data, device="cuda"):
        """
        Run Inferer over pre-processed Data.
        """
        start_time = time.time()
        
        try:
            # Load the 3D NIfTI image
            image_array, spacing, origin  = ImageProcessor.load_nifti(data["image"])

            # Initialize the predictor for new task
            self.initialize_predictor()
            
            # Reprocess if new volume 
            if data["State"] =='ProcessVolume':
            
            # if self.image_path != data["image"]:
                self.image_path = data["image"]
                
                #Load Frames using JPEG 
                # Convert slices in the array to JPEG images in axial orientation
                # ImageProcessor.convert_volume_to_frames(image_array, axis=2, output_dir=self.jpeg_filepath)
                
                #create an inference state (Note: Image Embeddings are calculated only here)
                # self.inference_state = self.predictor.init_state(video_path=self.jpeg_filepath)
                
                #Load Frames using Tensorflow directly(NEW)
                self.inference_state = self.predictor.init_state(video_path=image_array)
  
                #create an array equivilant to the image shape to carry segmentation 
                self.annotated_image = np.zeros_like(image_array, dtype=np.int32)
                
                #Keep track of the first promted slice that will be used
                self.StartSliceIndex=image_array.shape[2] 
                
                #Reset the object bounds 
                self.object_bounds={}
            
            #Process same volume     
            else:
                # If the inference state not defined ; load the cashed one        
                if (not hasattr(self, 'inference_state')) or (self.inference_state is None):
                    self.inference_state = StateManager.load_state(self.temp_state_path)

            # In case of 2d segmentation
            if data["State"]=='2d': 
                # Reset the predictor state
                self.predictor.reset_state(self.inference_state)
                self.segment_objects(data, image_array)
            
            elif data["State"] == 'Auxillary_Box':
                print("Auxillary_Box State Enteredd!!!")
                
                #Extract the box data
                box_arr = data['Auxillary_Box'][0]
                slice_index=data['Auxillary_Box'][1]
                orientation_axis =data['Auxillary_Box'][2]
                obj_id='1'
                
                print("box_arr",box_arr)
                print("slice_index",slice_index)
                print("orientation_axis",orientation_axis)
                
                #Extract slice data
                slice_data = image_array[slice_index,:,:]
                slice_normalized = ((slice_data - np.min(slice_data)) / (np.max(slice_data) - np.min(slice_data)) * 255).astype(np.uint8)

                print("image_array shape",image_array.shape)
                print("Shape of slice",slice_data.shape)
                #Remove directory if it exists
                if os.path.exists(self.jpeg_filepath):
                    try:
                        shutil.rmtree(self.jpeg_filepath)
                        print(f"Removed existing directory: {self.jpeg_filepath}")
                    except Exception as e:
                        print(f"Error removing directory: {e}")
                        raise

                #Create fresh directory
                try:
                    os.makedirs(self.jpeg_filepath, exist_ok=True)
                    print(f"Created clean directory: {self.jpeg_filepath}")
                except Exception as e:
                    print(f"Error creating directory: {e}")
                    raise
                
                # Convert to PIL Image and save as JPEG
                Image.fromarray(slice_normalized).save(os.path.join(self.jpeg_filepath, f"{slice_index:05}.jpg"))
                
                #initialize a new predictor and new inference with only one slice .
                auxillary_predictor = build_sam2_video_predictor(self.config_path, self.checkpoint_path, device=device)

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
                print("out_mask_logits:", out_mask_logits)
                print("obj_id:", out_obj_ids)

                # Convert tensor to NumPy array where self.out_mask_logits=[obj_id][chanel:0 for grayscale]
                obj_id_int = int(obj_id)
                print("self.object_bounds: 11111",self.object_bounds)
                
                mask_array =out_mask_logits[0][0].cpu().numpy()
                
                print("mask_array shape",mask_array.shape)
                
                if obj_id not in self.object_bounds:
                    self.object_bounds[obj_id] = [None, None]
                
                print("obj_id_int:",obj_id_int)
                print("self.object_bounds: 2222",self.object_bounds)
                
                # Calculate width metrics (column indices where mask > 0)
                nonzero_cols = np.where(np.any(mask_array > 0, axis=0))[0]  # Get columns with at least one positive pixel
                nonzero_row = np.where(np.any(mask_array > 0, axis=1))[0]
                
                if len(nonzero_row) > 0:
                    min_row = np.min(nonzero_row)
                    max_row = np.max(nonzero_row)
                    print(f"Segmentation spans rows: {min_row} to {max_row}")
                if len(nonzero_cols) > 0:
                    min_col = np.min(nonzero_cols)
                    max_col = np.max(nonzero_cols)
                    print(f"Segmentation spans columns: {min_col} to {max_col}")
                 
                # Update object_bounds (maintain overall min/max across all slices)
                    
                if self.object_bounds[obj_id][0] is None or min_col < self.object_bounds[obj_id][0]:
                    self.object_bounds[obj_id][0] = min_col
                if self.object_bounds[obj_id][1] is None or max_col > self.object_bounds[obj_id][1]:
                    self.object_bounds[obj_id][1] = max_col
                if(orientation_axis==0):
                            
                    reversed_slice_index=slice_index #No reversion in sagital 
                    
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
                    
                    #Clearing first
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
                    
       
                print(f"Current object bounds (width): {self.object_bounds[obj_id]}")
                
            # In case of 3d segmentation
            elif data["State"] =='3d':
                
                # Invert the start slicing index as OHIF slice numbering is reversed 
                print("self.StartSliceIndex in 3d ",self.StartSliceIndex)
                self.StartSliceIndex = image_array.shape[2] - int(self.StartSliceIndex) -1
                print("self.StartSliceIndex after inversion",self.StartSliceIndex)
                    
                #initialize a dictionary where segmentation are stored in 
                video_segments={}
                        
                # propagation in whole slices 
                for out_frame_idx, out_obj_ids, out_mask_logits in self.predictor.propagate_in_video(
                    self.inference_state, start_frame_idx=int(self.StartSliceIndex), reverse =True 
                ):
                    video_segments[out_frame_idx] = {
                        out_obj_id: (out_mask_logits[i] > 0.0).cpu().numpy()
                        for i, out_obj_id in enumerate(out_obj_ids)
                    }
                #clear old segmentation     
                self.annotated_image = np.zeros_like(image_array, dtype=np.int32)
                
                #reconstruct the jpeg slices into volume mask
                mask = ImageProcessor.reconstruct_nifti(video_segments) 
                
                #resize the mask to fit in the annotated image
                self.annotated_image=ImageProcessor.resize_to_match(self.annotated_image.shape,mask)
                
                # Apply object bounds filtering after reconstruction
                total_slices = image_array.shape[2]
                margin = int(0.05 * total_slices)  # 5% of total slices as margin
                print("margin",margin)
                
                for obj_id, bounds in self.object_bounds.items():
                    if bounds[0] is not None and bounds[1] is not None:
                        
                        # Calculate bounds with 5% margin
                        reversedBound= [image_array.shape[2] - bounds[0] -1,image_array.shape[2] - bounds[1] -1]
                        print("reversedBound 111",reversedBound)
                        if reversedBound[0]>reversedBound[1]:
                            reversedBound=[reversedBound[1],reversedBound[0]]
                            print("reversedBound 222",reversedBound)
                        
                        #keep marginal error 
                        lower_bound = max(0, reversedBound[0] - margin)
                        upper_bound = min(total_slices - 1, reversedBound[1] + margin)
                        print("lower_bound",lower_bound)
                        print("upper_bound",upper_bound)
                        
                        # Count slices BEFORE filtering
                        pre_slices = [
                            z for z in range(self.annotated_image.shape[2])
                            if np.any(self.annotated_image[:, :, z] == int(obj_id))
                        ]
                        print(f"[Before] Object {obj_id} exists in {len(pre_slices)} slices: {pre_slices}")

                        print(f"Filtering object {obj_id} - keeping slices {lower_bound} to {upper_bound}")
                        
                        # Filter slices for this object
                        for z in range(self.annotated_image.shape[2]):
                            if z < lower_bound or z > upper_bound:
                                # Clear segmentation outside bounds for this object
                                self.annotated_image[:, :, z] = np.where(
                                    self.annotated_image[:, :, z] == int(obj_id),
                                    0,
                                    self.annotated_image[:, :, z]
                                )
                                
                        # Count slices AFTER filtering
                        post_slices = [
                            z for z in range(self.annotated_image.shape[2])
                            if np.any(self.annotated_image[:, :, z] == int(obj_id))
                        ]
                        print(f"[After]  Object {obj_id} exists in {len(post_slices)} slices: {post_slices}")
                                
            data["pred"] = self.annotated_image #add the segmentation to the response for OHIF


            self.temp_state_path = StateManager.save_state(self.inference_state) #save the last version from infernence

        except Exception as e:
            # Ensure cleanup happens even if error occurs
            self.cleanup_inference_state()
            raise
        finally:
            # Always clean up
            self.cleanup_inference_state()
            logger.info(f"Execution time: {time.time() - start_time:.2f} seconds")
        

        return data
    
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
        for obj_id, prompt_details in data["samc2onfig"].items():
            
            print("at obj_id:",obj_id,"coressponds",prompt_details)
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
                    
                    print("points_with_frames",points_with_frames)
                    print("labels",labels)
                    print("points_within_triggered_slice",points_within_triggered_slice)
                    print("Labels_within_triggered_slice",Labels_within_triggered_slice)
                    
                    boxes_within_triggered_slice=[]
                    if len(boxes) > 0:
                        for box in boxes[0]:
                            if box[1] == frame_idx:
                                boxes_within_triggered_slice.append(box[0])
                            
                    print("boxes_within_triggered_slice",boxes_within_triggered_slice)
                    
                    if ( (len(boxes_within_triggered_slice) > 0) or (len(Labels_within_triggered_slice) >0) ):
                           
                        # Reverse the slice index as the slice numbering in OHIF is reversed 
                        reversed_frame_idx= image_array.shape[2] - frame_idx - 1
                        
                        #Apply 2d segmentation on that slice
                        self.segment_2d(reversed_frame_idx,obj_id,points_within_triggered_slice,Labels_within_triggered_slice,boxes_within_triggered_slice)
                    
                        #keep track of the min refrenced slice index for 3d segmentation  
                        print("frame_idx",frame_idx)   
                        print(" self.StartSliceIndex", self.StartSliceIndex)
                        
                        if frame_idx < self.StartSliceIndex:
                            self.StartSliceIndex = frame_idx
                            
                        print(" self.StartSliceIndex inside the loop ",self.StartSliceIndex)
 
                                                    
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
        print("out_mask_logits:", self.out_mask_logits)
        print("obj_id:", self.out_obj_ids)

        # Convert tensor to NumPy array where self.out_mask_logits=[obj_id][chanel:0 for grayscale]
        obj_id_int = int(obj_id)
        
        while obj_id_int > 0:
            try:
                mask_array = self.out_mask_logits[obj_id_int - 1][0].cpu().numpy()
                break  # Exit the loop if successful
            except:
                obj_id_int -= 1  # Decrement obj_id and try again
        
        # Clear the old segmentation
        print(f"Before clearing, slice {frame_idx} unique values: {np.unique(self.annotated_image[:, :, int(frame_idx)])}")

        self.annotated_image[:, :, int(frame_idx)] = np.where(
            self.annotated_image[:, :, int(frame_idx)] == int(obj_id),
            0,
            self.annotated_image[:, :, int(frame_idx)]
        )

        print(f"After clearing, slice {frame_idx} unique values: {np.unique(self.annotated_image[:, :, int(frame_idx)])}")

        # Add the new segmentation mask
        self.annotated_image[:, :, int(frame_idx)] = np.where(
            mask_array > 0,
            obj_id,
            self.annotated_image[:, :, int(frame_idx)]
        )

        print(f"After adding new mask, slice {frame_idx} unique values: {np.unique(self.annotated_image[:, :, int(frame_idx)])}")

   

    def inferer(self, data=None) -> SlidingWindowInferer:
        return SlidingWindowInferer(roi_size=[128, 128, 32], sw_batch_size=6, overlap=0.1)

    def pre_transforms(self, data=None) -> Sequence[Callable]:
        return []

    def post_transforms(self, data=None) -> Sequence[Callable]:
        return []
    
