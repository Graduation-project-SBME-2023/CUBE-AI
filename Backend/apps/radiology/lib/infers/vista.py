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
import json
import subprocess
import os
import nibabel as nib
import logging


from monai.inferers import Inferer

from monailabel.tasks.infer.basic_infer import BasicInferTask
from monai.data import MetaTensor
from monailabel.interfaces.tasks.infer_v2 import InferType
import torch
import tempfile
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)



def convert_to_serializable(obj):
    if isinstance(obj, MetaTensor):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {k: convert_to_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_serializable(item) for item in obj]
    return obj

class VISTAPOINT3D(BasicInferTask):
    """
    This provides Inference Engine for pre-trained VISTA segmentation model.
    """

    def __init__(
        self,
        path,
        network=None,
        target_spacing=(1.5, 1.5, 1.5),
        type=InferType.SEGMENTATION,
        labels=None,
        dimension=2,
        description="A pre-trained model for volumetric (2.5D) segmentation of the monai vista",
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
        self.BASE_PATH = os.getenv("MODELS_DIR")


    def is_valid(self) -> bool:
        return True

    def pre_transforms(self, data=None) -> Sequence[Callable]:
        return []
    
    def adjust_points(self, data):
        """
        Adjusts the Z coordinate and swaps X/Y for all points.
        """
        image_array = nib.load(data["image"]).get_fdata()
        z_dim = image_array.shape[2]

        for point in data["Points"]['points']:
            # Flip Z coordinate
            point[2] = z_dim - point[2] - 1
            # Swap X and Y
            point[0], point[1] = point[1], point[0]

        print("Updated Points:", data["Points"]['points'])
    
    def run_monai_inference(self,config_path, image_path, mode, label_index=None, point=None,point_labels=None):
        """
        Updates the `input_dict` field in the MONAI inference.json file and runs inference.

        Parameters:
        - config_path (str): Path to inference.json file.
        - image_path (str): Path to the input medical image.
        - label_index (int): Label index for segmentation.
        - point (list): List of [x, y, z] coordinates for the prompt.
        """
        config_dir = os.path.dirname(config_path)
        print("config_dir", config_dir)
        # Ensure the config directory exists
        print(os.listdir(config_dir))  # Prints all files in the directory


        # Load the current inference.json file
        with open(config_path, "r") as f:
            config = json.load(f)

        # Update the `input_dict` field
        if mode=="interactive":
            config["input_dict"] = {
                "image": image_path,
                "label_prompt": [label_index],
                "points": point,  # Ensure it's a list of lists
                "point_labels": point_labels  # Assuming 1 is the default class label
            }
        else:
            config["input_dict"] = {
                "image": image_path,
                "label_prompt": [label_index],
            }

        config['bundle_root'] = self.BASE_PATH
        config['checkpointloader']['load_path'] = self.path[0]
        config['metadata_path'] = self.BASE_PATH + '/configs/vista/metadata.json'

        self.output_dir = config['output_dir']

        config = convert_to_serializable(config)

        with open(config_path, "w") as f:
            json.dump(config, f, indent=4)

        # Run the MONAI inference command from the target directory
        command = f'python -m monai.bundle run --config_file "{config_path}"'
        subprocess.run(command, shell=True, check=True, cwd=self.BASE_PATH)

    def run_inferer(self, data, convert_to_batch=True, device="cuda"):

        # make data inside points list ( int )
        # data["Points"]['points'] = [list(map(int, point)) for point in data["Points"]['points']]
        points = None
        point_labels = None

        if data["Mode"] == "interactive" and "Points" in data:
            self.adjust_points(data)  # Adjust points before inference
            points = data["Points"]['points']
            # Use point_labels if available
            if 'labels' in data["Points"]:
                point_labels = data["Points"]['labels']


        print("Running MONAI VISTA Inference...")
        config_path = os.path.join(self.BASE_PATH, "configs", "vista", "inference.json")

        self.run_monai_inference(
            config_path=config_path,
            image_path=data[self.input_key],
            mode=data["Mode"],
            label_index=data["Class"],
            point=points,
            point_labels=point_labels
        )
        
        if device.startswith("cuda"):
            torch.cuda.empty_cache()

        print("maskkkk",data[self.input_key])

        file_ending = "_trans.nii.gz"
        basename = (    
            os.path.basename(data[self.input_key])
        )
        basename = re.sub(r"\.nii\.gz$", "", basename)
        print("basename", basename) 

        output_path = os.path.join(self.output_dir, f"{basename}/{basename}{file_ending}")

        if os.path.exists(output_path):
            outputs = nib.load(output_path).get_fdata()
            outputs = torch.from_numpy(outputs)
            print("outputs shape", outputs.shape)
            # see unique values in the outputs
            unique_values = torch.unique(outputs)
            # make class 255 to 0
            outputs[outputs == 255] = 0
            print("Unique values in outputs:", unique_values)
            # shutil.rmtree(output_dir)

        data[self.output_label_key] = outputs

        return data


    def inverse_transforms(self, data=None):
        return []

    def post_transforms(self, data=None) -> Sequence[Callable]:
        return []