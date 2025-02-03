import os
import subprocess
import logging
from monailabel.tasks.train.basic_train import BasicTrainTask, Context
from nnunetv2.training.nnUNetTrainer.nnUNetTrainer import nnUNetTrainer
import shutil
from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor


logger = logging.getLogger(__name__)


class GenericSegmentation(BasicTrainTask):
    def __init__(  # change
        self,
        model_dir,
        task_name,
        fold=0,
        plan_type="2d",
        root_nnunet_dir=r"C:\Users\Lenovo\Desktop\Grad\Monai\radiology\nnunet",
        trainer_class="nnUNetTrainer",
        description="Train and infer using nnUNet for segmentation",
        **kwargs,
    ):
        # Initialize the nnUNet Trainer Task.
        # :param model_dir: Path to the model directory
        # :param task_name: Name of the nnUNet task (e.g., TaskXXX_MyTask)
        # :param fold: Fold to train (default: 0)
        # :param trainer_class: Trainer class name in nnUNet
        # :param plans_file: Name of the nnUNet plans file
        self.task_name = task_name
        self.fold = fold
        self.trainer_class = trainer_class 
        self.model_dir=model_dir
        self.plan_type = plan_type


        root_nnunet_dir = root_nnunet_dir
        os.environ["nnUNet_raw"] = os.path.join(root_nnunet_dir, "nnUNet_raw")
        os.environ["nnUNet_preprocessed"] = os.path.join(root_nnunet_dir, "nnUNet_Preprocessed")
        os.environ["nnUNet_results"] = os.path.join(root_nnunet_dir, "nnUNet_Results")

        
        # Extract the numeric part from the task name
        task_id = ''.join(filter(str.isdigit, task_name))

        # Assign the extracted task ID to self.task_id
        self.task_id = task_id        
    
        super().__init__(model_dir, description, **kwargs)
    def network(self, context: Context):
        # nnUNet handles network creation internally, so this is not overridden.
        return None  # nnUNet internally initializes its network via the trainer.

    def optimizer(self, context: Context):
        # Optimizer is managed internally by nnUNet, so it is not overridden.
        return None  # nnUNet Trainer handles the optimizer.

    def loss_function(self, context: Context):
        # Loss function is managed internally by nnUNet, so it is not overridden.
        return None  # nnUNet Trainer handles the loss function.
    
     
    def preprocess_dataset(self):
        logger.info("Starting nnUNet Dataset Preprocessing")

        # Check if preprocessing is already done
        preprocessed_data_path = os.path.join(os.environ["nnUNet_preprocessed"], self.task_name)

        # Build the nnUNet preprocessing command
        command = [    # change
            "nnUNetv2_plan_and_preprocess",
            "-d", self.task_id,
            "--verify_dataset_integrity"
        ]

        logger.info(f"Running command: {{' '.join(command)}}")

        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Preprocessing failed with error: {{e}}")
            raise RuntimeError(f"Preprocessing failed for dataset {{self.task_name}}","error: {{e}}")

        logger.info(f"Preprocessed data saved to: {{preprocessed_data_path}}")

        return preprocessed_data_path

    def train(self, rank: int, world_size: int, request, datalist):
        # Train the model using the nnUNet CLI.
        logger.info("Starting nnUNet Training")

        # Ensure preprocessing is complete
        self.preprocess_dataset()

        # Run the nnUNet training command
        command = [      
            "nnUNetv2_train",
             self.task_id, 
             str(self.plan_type),  
             str(self.fold), 
            "-tr", self.trainer_class, 
        ]
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Training failed with error: {e}")
            raise RuntimeError(f"Training failed for dataset {self.task_name}, fold {self.fold}, error: {e}")

        check_point_path = os.path.join(
        os.environ["nnUNet_results"],
        self.task_name,
        f"{self.trainer_class}__nnUNetPlans__{self.plan_type}",
        f"fold_{self.fold}",
        "checkpoint_best.pth"
    )
        
        model_name=self.task_name.split("_")[1]

        # move checkpoint_best.pth to model_dir and give it name model.pth
        # TODO: check if the this is the correct place to move checkpoint to it
        
        logger.info("saving checkpoint to model_dir")

        shutil.copy(check_point_path, os.path.join(self.model_dir, f"pretrained_{model_name}.pt"))

        logger.info("nnUNet Training Completed")

        # TODO: initialize inference task


    def val_inferer(self, context: Context):
        # Inference logic for nnUNet.

        logger.info("Setting up nnUNet Predictor for Validation")

        # Initialize the nnUNet Predictor
        predictor = nnUNetPredictor(
            tile_step_size=0.5,  # Overlap size during sliding window inference
            device=context.device,
            verbose=True,
            verbose_preprocessing=True,
            allow_tqdm=True,
        )

        # Load the trained model
        predictor.initialize_from_trained_model_folder(
            os.path.join(os.environ["nnUNet_results"], self.task_name, self.trainer_class),
            use_folds=(self.fold,),
            checkpoint_name="checkpoint_best.pth",
        )

        return predictor

    def train_pre_transforms(self, context: Context):
        # nnUNet handles its own preprocessing. This is not needed.
        return None  # nnUNet handles preprocessing internally during training.

    def train_post_transforms(self, context: Context):
        # nnUNet handles its own postprocessing. This is not needed.
        return None  # nnUNet handles postprocessing internally.

    def val_pre_transforms(self, context: Context):
        # nnUNet handles its own validation preprocessing.
        return None  # nnUNet handles preprocessing internally during validation.

    def val_post_transforms(self, context: Context):
        # nnUNet handles its own validation postprocessing.
        return None  # nnUNet handles postprocessing internally
