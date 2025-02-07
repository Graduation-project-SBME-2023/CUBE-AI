import torch
import logging
import copy
import os
import nibabel as nib
from enum import Enum
from typing import Any, Callable, Dict, Sequence, Tuple, Union


from monai.inferers import Inferer, SlidingWindowInferer, SliceInferer
from monailabel.interfaces.tasks.infer_v2 import InferType
from monailabel.tasks.infer.basic_infer import BasicInferTask
from monailabel.interfaces.utils.transform import dump_data
from monailabel.utils.others.generic import name_to_device

from batchgenerators.utilities.file_and_folder_operations import join
from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor
from pathlib import Path    


logger = logging.getLogger(__name__)


class GenericSegmentation(BasicInferTask):
    def __init__(
        self,
        path,
        root_nnunet_dir,
        type=InferType.SEGMENTATION,
        labels=None,
        network=None,
        model_name=None,
        plan_type="2d",
        fold=0,
        dimension=2,
        description="A pre-trained NnUnet model for volumetric segmentation ",
        **kwargs,
    ):
        super().__init__(
            path=path,
            type=type,
            labels=labels,
            network=network,
            dimension=dimension,
            description=description,
            load_strict=True,
            **kwargs,
        )

        self.model_name=model_name
        self.plan_type = plan_type
        self.fold = fold
        self.root_nnunet_dir = root_nnunet_dir
        # TODO : remove this hardcoding
        self.root_nnunet_dir = r"C:\Users\Lenovo\Desktop\Grad\Monai\radiology\nnunet"



        self.temp_path = "/temp"

        os.environ["nnUNet_raw"] = os.path.join(self.root_nnunet_dir, "nnUNet_raw")
        os.environ["nnUNet_preprocessed"] = os.path.join(self.root_nnunet_dir, "nnUNet_Preprocessed")
        os.environ["nnUNet_results"] = os.path.join(self.root_nnunet_dir, "nnUNet_Results")

        logging.info(f"nnUNet_results: {os.environ['nnUNet_results']}")


        self.task_name = self.get_data_name_from_model_name(self.model_name)


    def get_data_name_from_model_name(self, model_name):
        """
        Search for the full dataset name in the nnUNet_raw directory using only the model name.
        Example: If model_name = "blastoma", and dataset folder is "Dataset200_blastoma",
        this function returns "Dataset200_blastoma".
        """
        nnunet_raw_dir = os.environ.get("nnUNet_raw")
        if not nnunet_raw_dir or not os.path.exists(nnunet_raw_dir):
            raise FileNotFoundError(f"nnUNet_raw directory not found: {nnunet_raw_dir}")

        # Search for a directory that ends with the given model name
        for dir_name in os.listdir(nnunet_raw_dir):
            if dir_name.endswith(f"_{model_name}"):  # Match DatasetXXX_{model_name}
                return dir_name  # Return full dataset name

        raise ValueError(f"No dataset found for model name: {model_name} in {nnunet_raw_dir}")

    
    def run_inferer(self, data, convert_to_batch=True, device="cuda"):
        # Run Inferer over pre-processed Data.  Derive this logic to customize the normal behavior.
        # In some cases, you want to implement your own for running chained inferers over pre-processed data

        # :param data: pre-processed data
        # :param convert_to_batch: convert input to batched input
        # :param device: device type run load the model and run inferer
        # :return: updated data with output_key
        
        predictor = nnUNetPredictor(
            tile_step_size=0.5,  # 0.5 // old value
            device=torch.device("cuda"),
            verbose=True,
            verbose_preprocessing=True,
            allow_tqdm=True,
        )

        # Initializes the network architecture, loads the checkpoint
        predictor.initialize_from_trained_model_folder(    
            join(
                os.environ["nnUNet_results"],
                f"{self.task_name}\\nnUNetTrainer__nnUNetPlans__{self.plan_type}",
            ),
            use_folds=(self.fold,), 
            checkpoint_name="checkpoint_best.pth",
        )

        seg = predictor.predict_from_files(
            [[data[self.input_key]]],
            self.temp_path,
            save_probabilities=False,
            overwrite=True,
            num_processes_preprocessing=4,  # worker
            num_processes_segmentation_export=4,  # worker
            folder_with_segs_from_prev_stage=None,
            num_parts=1,
            part_id=0,
        )

        if device.startswith("cuda"):
            torch.cuda.empty_cache()

        file_ending = ".nii"
        basename = (
            os.path.basename(data[self.input_key])[: -(len(file_ending) + 5)]
            + file_ending
        )
        output_path = join(self.temp_path, basename)

        if os.path.exists(output_path): 
        # if output_path: 
            outputs = nib.load(output_path).get_fdata()
            outputs = torch.from_numpy(outputs)
            os.remove(output_path)

        data[self.output_label_key] = outputs

        return data

    def pre_transforms(self, data=None) -> Sequence[Callable]:
        return []

    def inferer(self, data=None) -> Inferer:
        return SlidingWindowInferer(
            roi_size=[128, 128, 32], sw_batch_size=6, overlap=0.1
        )

    def inverse_transforms(self, data=None):
        return []

    def post_transforms(self, data=None) -> Sequence[Callable]:
        return []

