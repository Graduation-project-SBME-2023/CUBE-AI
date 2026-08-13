from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Callable, Sequence

import nibabel as nib
import torch
from monai.inferers import Inferer, SlidingWindowInferer
from monailabel.interfaces.tasks.infer_v2 import InferType
from monailabel.tasks.infer.basic_infer import BasicInferTask
from monailabel.utils.others.generic import name_to_device
from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor
from storage_paths import TEMP_DIR

from radiology_app.integrations.nnunet import (
    configure_nnunet_environment,
    resolve_dataset,
)

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
        trainer_class="CustomNnUnetTrainer",
        description="A trained nnUNet model for volumetric segmentation",
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
        self.model_name = model_name
        self.plan_type = plan_type
        self.fold = fold
        self.trainer_class = trainer_class
        self.nnunet_paths = configure_nnunet_environment(root_nnunet_dir)
        self.task_name, _ = resolve_dataset(
            self.nnunet_paths.raw,
            self.model_name,
        )
        self.temp_root = TEMP_DIR / "radiology"
        self.temp_root.mkdir(parents=True, exist_ok=True)
        logger.info("Resolved nnUNet dataset: %s", self.task_name)

    def run_inferer(self, data, convert_to_batch=True, device="cuda"):
        predictor = nnUNetPredictor(
            tile_step_size=0.5,
            device=name_to_device(device),
            verbose=True,
            verbose_preprocessing=True,
            allow_tqdm=True,
        )
        predictor.initialize_from_trained_model_folder(
            str(
                self.nnunet_paths.results
                / self.task_name
                / f"{self.trainer_class}__nnUNetPlans__{self.plan_type}"
            ),
            use_folds=(self.fold,),
            checkpoint_name="checkpoint_best.pth",
        )

        with tempfile.TemporaryDirectory(
            dir=self.temp_root,
            prefix=f"{self.model_name}-infer-",
        ) as output_dir:
            predictor.predict_from_files(
                [[data[self.input_key]]],
                output_dir,
                save_probabilities=False,
                overwrite=True,
                num_processes_preprocessing=4,
                num_processes_segmentation_export=4,
                folder_with_segs_from_prev_stage=None,
                num_parts=1,
                part_id=0,
            )

            output_files = sorted(Path(output_dir).glob("*.nii.gz"))
            if len(output_files) != 1:
                raise RuntimeError(
                    f"Expected one nnUNet output for {self.model_name}; "
                    f"found {len(output_files)} in {output_dir}"
                )
            logger.info("Loading nnUNet segmentation: %s", output_files[0])
            outputs = torch.from_numpy(nib.load(output_files[0]).get_fdata())

        if str(device).startswith("cuda"):
            torch.cuda.empty_cache()
        data[self.output_label_key] = outputs
        return data

    def pre_transforms(self, data=None) -> Sequence[Callable]:
        return []

    def inferer(self, data=None) -> Inferer:
        return SlidingWindowInferer(
            roi_size=[128, 128, 32],
            sw_batch_size=6,
            overlap=0.1,
        )

    def inverse_transforms(self, data=None):
        return []

    def post_transforms(self, data=None) -> Sequence[Callable]:
        return []
