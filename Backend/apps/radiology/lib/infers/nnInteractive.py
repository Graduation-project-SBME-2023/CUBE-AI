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

from typing import Callable, Sequence
import os
import nibabel as nib
from typing import Optional, Tuple
import numpy as np
from nnInteractive.inference.inference_session import nnInteractiveInferenceSession
import time

from monai.inferers import Inferer

from monailabel.tasks.infer.basic_infer import BasicInferTask
from monai.data import MetaTensor
from monailabel.interfaces.tasks.infer_v2 import InferType
import torch
import tempfile
import re
import shutil
from pathlib import Path
import SimpleITK as sitk
import logging



logger = logging.getLogger(__name__)


def convert_to_serializable(obj):
    if isinstance(obj, MetaTensor):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {k: convert_to_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_serializable(item) for item in obj]
    return obj

class NNINTERACTIVE(BasicInferTask):
    """
    This provides Inference Engine for pre-trained nnInteractive segmentation model.
    """

    def __init__(
        self,
        path,
        network=None,
        target_spacing=(1.5, 1.5, 1.5),
        type=InferType.SEGMENTATION,
        labels=None,
        dimension=2,
        description="A pre-trained model for volumetric  segmentation of the monai nnInteractive",
        **kwargs,
    ):
        super().__init__(
            path=path,
            network=network,
            type=type,
            labels=labels,
            dimension=dimension,
            description=description,
            **kwargs,
        )
        self.target_spacing = target_spacing
        radiology_path =Path("/apps/radiology")
        self.BASE_PATH =radiology_path
        self.session=None


    def is_valid(self) -> bool:
        return True

    def pre_transforms(self, data=None) -> Sequence[Callable]:
        return []

    def adjust_points(self, data, viewport=None):
        """
        Adjusts the Z coordinate and swaps X/Y for all points.
        """
        image_array = nib.load(data["image"]).get_fdata()
        x_dim = image_array.shape[0]
        y_dim = image_array.shape[1]
        z_dim = image_array.shape[2] 
        updated_points = []
        x = data["Points"]['points'][-1]["0"]
        y = data["Points"]['points'][-1]["1"]
        z = data["Points"]['points'][-1]["2"]
        point = (int(x), int(y), int(z))
        updated_points=point
        print("Updated Points in  view:", updated_points)


        data["Points"]['points']= updated_points


    # Intilization function
    def initialize_session(self):
        # Initialize inference session
        session = nnInteractiveInferenceSession(
            device=torch.device("cuda"),  # Use CPU
            use_torch_compile=False,
            verbose=True,
            torch_n_threads=os.cpu_count(),
            do_autozoom=True,
            use_pinned_memory=True,
        )
        return session

    # load the model
    def load_model(self,session, model_path):
        try:
            session.initialize_from_trained_model_folder(model_path)
            logger.info("Model loaded successfully!")
        except Exception as e:
            logger.error(f"Error loading model: {e}")


    def load_volume(self,volume_path):
        input_image = sitk.ReadImage(volume_path)
        arr = sitk.GetArrayFromImage(input_image)          # (Z, Y, X)
        arr = np.transpose(arr, (2, 1, 0))              # reorder to (X, Y, Z)
        img_tensor = torch.from_numpy(arr).float().unsqueeze(0)  # (1, X, Y, Z)

        return img_tensor

    # def nnInteractive_segmentation(self,volume_path,
    #                            output_path,
    #                            model_path,
    #                            point_coordinates: tuple,
    #                            Interactive: bool = True,
    #                            new_object=False,
    #                            reset_session: bool = False,
    #                            session: Optional[nnInteractiveInferenceSession] = None) -> nnInteractiveInferenceSession:
    #     img = self.load_volume(volume_path)
    #     # Session management
    #     if session is None or reset_session or (
    #     hasattr(session, "current_shape") and session.current_shape != img.shape
    # ):
    #         session = self.initialize_session()
    #         # img=self.load_volume(volume_path)
    #         session.set_image(img.numpy())
    #         target_tensor = torch.zeros(img.shape[1:], dtype=torch.uint8)
    #         session.set_target_buffer(target_tensor)
    #     elif new_object:
    #         session.reset_interactions()

    #     # Load model if not already loaded
    #     if not hasattr(session, 'model') or reset_session:
    #         self.load_model(session, model_path)


    #     POINT_COORDINATES = point_coordinates # points in (x,y,z)   

    #     logger.info(f"====== points: {POINT_COORDINATES} ======")    

    #     session.add_point_interaction(POINT_COORDINATES, include_interaction=Interactive)

    #     results = session.target_buffer.clone()

    #     results_np = results.cpu().numpy()

    #     # Load the original image to get spatial information
    #     ref_img = nib.load(volume_path)
    #     affine = ref_img.affine

    #     # Save the transformed segmentation result as a NIfTI file
    #     seg_img = nib.Nifti1Image(results_np, affine)

    #     os.makedirs(output_path, exist_ok=True)

    #     basename = os.path.basename(volume_path)
    #     basename = re.sub(r"\.nii\.gz$", "", basename)
    #     timestamp = time.strftime("%Y%m%d-%H%M%S")
    #     output_file = os.path.join(output_path, f"{basename}_seg_{timestamp}.nii.gz")

    #     nib.save(seg_img, output_file)
    #     print(f"Segmentation result saved as {output_file}")

    #     return session, output_file

    
    def nnInteractive_segmentation(self,volume_path,
                        output_path,
                        model_path,
                        point_coordinates: tuple,
                        Interactive: bool = True,
                        new_object=False,
                        reset_session: bool = False,
                        session: Optional[nnInteractiveInferenceSession] = None) -> nnInteractiveInferenceSession:
        img = self.load_volume(volume_path)
        
        # Store the current image info for comparison
        current_shape = img.shape
        current_volume_path = volume_path
        
        # Session management - Enhanced condition checking
        should_reset_session = (
            session is None or 
            reset_session or 
            not hasattr(session, '_current_image_shape') or 
            session._current_image_shape != current_shape or
            not hasattr(session, '_current_volume_path') or
            session._current_volume_path != current_volume_path
        )
        
        if should_reset_session:
            logger.info(f"Initializing new session. Reason: {'no session' if session is None else 'reset requested' if reset_session else 'different image'}")
            session = self.initialize_session()
            session.set_image(img.numpy())
            target_tensor = torch.zeros(img.shape[1:], dtype=torch.uint8)
            session.set_target_buffer(target_tensor)
            
            # Store session metadata for future comparisons
            session._current_image_shape = current_shape
            session._current_volume_path = current_volume_path
            
            # Load model for new session
            self.load_model(session, model_path)
        elif new_object:
            logger.info("Resetting interactions for new object on same image")
            session.reset_interactions()
            # Reset target buffer for new object
            if hasattr(session, 'target_buffer') and session.target_buffer is not None:
                if isinstance(session.target_buffer, torch.Tensor):
                    session.target_buffer.zero_()
                elif isinstance(session.target_buffer, np.ndarray):
                    session.target_buffer.fill(0)

        # Load model if not already loaded (for existing sessions)
        if not hasattr(session, 'model'):
            self.load_model(session, model_path)

        POINT_COORDINATES = point_coordinates # points in (x,y,z)   

        logger.info(f"====== points: {POINT_COORDINATES} ======")    

        session.add_point_interaction(POINT_COORDINATES, include_interaction=Interactive)

        results = session.target_buffer.clone()

        results_np = results.cpu().numpy()

        # Load the original image to get spatial information
        ref_img = nib.load(volume_path)
        affine = ref_img.affine

        # Save the transformed segmentation result as a NIfTI file
        seg_img = nib.Nifti1Image(results_np, affine)

        os.makedirs(output_path, exist_ok=True)

        basename = os.path.basename(volume_path)
        basename = re.sub(r"\.nii\.gz$", "", basename)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        output_file = os.path.join(output_path, f"{basename}_seg_{timestamp}.nii.gz")

        nib.save(seg_img, output_file)
        print(f"Segmentation result saved as {output_file}")

        return session, output_file


    def run_inferer(self, data, convert_to_batch=True, device="cuda"):

        print("Running nnInteractive Inference...")
        print("data", data)
        print("view port", data["Points"]['viewPort'])

        # adjust points
        self.adjust_points(data,viewport=data["Points"]['viewPort'])

        # check point label
        print("point label", data["Points"]['labels'])

        self.session, output_path=self.nnInteractive_segmentation(
            volume_path=data[self.input_key],
            output_path=os.path.join(self.BASE_PATH,"Data","output"),
            model_path=os.path.join(self.BASE_PATH,"nnInteractive_v1.0"),
            point_coordinates=data["Points"]['points'],  # Example coordinates
            Interactive=data['Points']['labels'][0],
            new_object=data['resetInteractions'],
            session=self.session,
        )
        if device.startswith("cuda"):
            torch.cuda.empty_cache()


        file_ending = ".nii.gz"
        basename = (
            os.path.basename(data[self.input_key])
        )
        basename = re.sub(r"\.nii\.gz$", "", basename)
        print("basename", basename)

        # output_path = os.path.join(self.BASE_PATH, "Data", "output","segmentation_result.nii.gz")

        if os.path.exists(output_path):
            outputs = nib.load(output_path).get_fdata()
            outputs = torch.from_numpy(outputs)
            unique_values = torch.unique(outputs)
            logger.info(f"[INFO] Unique values in mask: {unique_values}")
            data[self.output_label_key] = outputs
        else:
            logger.error(f"Expected output file not found: {output_path}")
            data[self.output_label_key] = None

        return data


    def inverse_transforms(self, data=None):
        return []

    def post_transforms(self, data=None) -> Sequence[Callable]:
        return []
