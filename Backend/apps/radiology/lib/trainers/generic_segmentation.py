from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

import requests
from monailabel.interfaces.utils.app import app_instance
from monailabel.tasks.train.basic_train import BasicTrainTask, Context
from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor

from radiology_app.integrations.nnunet import (
    configure_nnunet_environment,
    resolve_dataset,
)

BACKEND_SYNC_URL = os.environ.get(
    "APP_API_URL",
    os.environ.get("CONTROL_API_URL", "http://application-api:8001"),
).rstrip("/") + "/models/sync"

logger = logging.getLogger(__name__)


class GenericSegmentation(BasicTrainTask):
    """Train a workflow-defined model through the nnUNet command-line tools."""

    def __init__(
        self,
        model_dir,
        model_name,
        labels,
        root_nnunet_dir,
        fold=0,
        plan_type="2d",
        trainer_class="CustomNnUnetTrainer",
        description="Train and infer using nnUNet for segmentation",
        **kwargs,
    ):
        self.labels = labels
        self.model_name = model_name
        self.fold = fold
        self.trainer_class = trainer_class
        self.model_dir = model_dir
        self.plan_type = plan_type
        self.nnunet_paths = configure_nnunet_environment(root_nnunet_dir)
        self.task_name, self.task_id = resolve_dataset(
            self.nnunet_paths.raw,
            self.model_name,
        )
        logger.info(
            "nnUNet paths: raw=%s; preprocessed=%s; results=%s",
            self.nnunet_paths.raw,
            self.nnunet_paths.preprocessed,
            self.nnunet_paths.results,
        )
        super().__init__(model_dir, description, config={"multi_gpu": False}, **kwargs)

    def network(self, context: Context):
        return None

    def optimizer(self, context: Context):
        return None

    def loss_function(self, context: Context):
        return None

    def preprocess_dataset(self) -> Path:
        logger.info("Starting nnUNet preprocessing for %s", self.task_name)
        command = [
            "nnUNetv2_plan_and_preprocess",
            "-d",
            self.task_id,
            "--verify_dataset_integrity",
            "-np",
            "3",
        ]
        logger.info("Running command: %s", " ".join(command))
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Preprocessing failed for dataset {self.task_name}"
            ) from exc

        output = self.nnunet_paths.preprocessed / self.task_name
        logger.info("Preprocessed data saved to: %s", output)
        return output

    def notify_backend_sync(self) -> None:
        try:
            response = requests.post(BACKEND_SYNC_URL, timeout=15)
            response.raise_for_status()
            logger.info("Backend sync triggered: %s", response.json())
        except (requests.RequestException, ValueError) as exc:
            # Synchronization is advisory and must not invalidate training.
            logger.warning("Backend sync notification failed: %s", exc)

    def train(self, rank: int, world_size: int, request, datalist) -> None:
        logger.info("Starting nnUNet training for %s", self.task_name)
        self.preprocess_dataset()
        command = [
            "nnUNetv2_train",
            self.task_id,
            str(self.plan_type),
            str(self.fold),
            "-tr",
            self.trainer_class,
        ]
        logger.info("Running command: %s", " ".join(command))
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Training failed for {self.task_name}") from exc

        trained_model_dir = (
            self.nnunet_paths.results
            / self.task_name
            / f"{self.trainer_class}__nnUNetPlans__{self.plan_type}"
        )
        checkpoint = trained_model_dir / f"fold_{self.fold}" / "checkpoint_best.pth"
        if not checkpoint.is_file():
            raise FileNotFoundError(
                f"nnUNet training completed without checkpoint: {checkpoint}"
            )

        destination = Path(self.model_dir) / f"pretrained_{self.model_name}.pt"
        shutil.copy2(checkpoint, destination)
        logger.info("Published nnUNet checkpoint: %s", destination)
        self.initialize_inferer()

    def initialize_inferer(self) -> None:
        logger.info("Refreshing inference task for %s", self.model_name)
        instance = app_instance()
        try:
            instance.initialize_inference_task(self.model_name)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to initialize inference task for model {self.model_name}"
            ) from exc
        self.notify_backend_sync()

    def val_inferer(self, context: Context):
        predictor = nnUNetPredictor(
            tile_step_size=0.5,
            device=context.device,
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
        return predictor

    def train_pre_transforms(self, context: Context):
        return None

    def train_post_transforms(self, context: Context):
        return None

    def val_pre_transforms(self, context: Context):
        return None

    def val_post_transforms(self, context: Context):
        return None

    def pre_process(self, request, datastore):
        return None
