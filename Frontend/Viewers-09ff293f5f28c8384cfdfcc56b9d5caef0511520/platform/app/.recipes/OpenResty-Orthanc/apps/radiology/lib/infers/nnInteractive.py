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
            print("Model loaded successfully!")
        except Exception as e:
            print(f"Error loading model: {e}")


    def load_volume(self,volume_path):
        input_image = sitk.ReadImage(volume_path)
        arr = sitk.GetArrayFromImage(input_image)          # (Z, Y, X)
        arr = np.transpose(arr, (2, 1, 0))              # reorder to (X, Y, Z)
        img_tensor = torch.from_numpy(arr).float().unsqueeze(0)  # (1, X, Y, Z)

        return img_tensor

    def nnInteractive_segmentation(self,volume_path,
                               output_path,
                               model_path,
                               point_coordinates: tuple,
                               Interactive: bool = True,
                               new_object=False,
                               reset_session: bool = False,
                               session: Optional[nnInteractiveInferenceSession] = None) -> nnInteractiveInferenceSession:

        # Session management
        if session is None or reset_session:
            session = self.initialize_session()
            img=self.load_volume(volume_path)
            session.set_image(img.numpy())
            target_tensor = torch.zeros(img.shape[1:], dtype=torch.uint8)
            session.set_target_buffer(target_tensor)
        elif new_object:
            session.reset_interactions()

        # Load model if not already loaded
        if not hasattr(session, 'model') or reset_session:
            self.load_model(session, model_path)


        POINT_COORDINATES = point_coordinates # points in (x,y,z)   

        logger.info("pointssssss",POINT_COORDINATES)    

        session.add_point_interaction(POINT_COORDINATES, include_interaction=Interactive)

        results = session.target_buffer.clone()

        results_np = results.cpu().numpy()

        # Load the original image to get spatial information
        ref_img = nib.load(volume_path)
        affine = ref_img.affine

        # Save the transformed segmentation result as a NIfTI file
        seg_img = nib.Nifti1Image(results_np, affine)

        os.makedirs(output_path, exist_ok=True)

        nib.save(seg_img, os.path.join(output_path, "segmentation_result.nii.gz"))

        print("Segmentation result saved as segmentation_result.nii.gz")


        return session



    def run_inferer(self, data, convert_to_batch=True, device="cuda"):

        print("Running nnInteractive Inference...")
        print("data", data)
        print("view port", data["Points"]['viewPort'])

        # adjust points
        self.adjust_points(data,viewport=data["Points"]['viewPort'])

        # check point label
        print("point label", data["Points"]['labels'])

        self.session=self.nnInteractive_segmentation(
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

        output_path = os.path.join(self.BASE_PATH, "Data", "output","segmentation_result.nii.gz")

        if os.path.exists(output_path):
            outputs = nib.load(output_path).get_fdata()
            outputs = torch.from_numpy(outputs)
            # see unique values in the outputs
            unique_values = torch.unique(outputs)
            # make class 255 to 0
            os.remove(output_path)

        data[self.output_label_key] = outputs

        return data


    def inverse_transforms(self, data=None):
        return []

    def post_transforms(self, data=None) -> Sequence[Callable]:
        return []
