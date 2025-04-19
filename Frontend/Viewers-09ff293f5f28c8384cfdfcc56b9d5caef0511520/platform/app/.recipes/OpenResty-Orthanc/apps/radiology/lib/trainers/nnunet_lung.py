# Trainer for nnunet_lung
import os
import subprocess
import logging
from monailabel.tasks.train.basic_train import BasicTrainTask, Context
from nnunetv2.training.nnUNetTrainer.nnUNetTrainer import nnUNetTrainer
from nnunetv2.paths import nnUNet_preprocessed, nnUNet_results
# from nnunetv2.utilities.dataset_name_id_conversion import maybe_convert_to_dataset_name

logger = logging.getLogger(__name__)



class NnunetLung(BasicTrainTask):
    def __init__(  # change
        self,
        model_dir,
        task_name,
        fold,
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


        task = "task2"  # Replace with the actual task name or pass it as a parameter
        root_nnunet_dir=r"D:\Liver Segmentation Meena 2024\monai_radiology_app\radiology\nnunet"
        os.environ["nnUNet_raw"] = os.path.join(root_nnunet_dir, task, "nnUNet_raw")
        os.environ["nnUNet_preprocessed"] = os.path.join(root_nnunet_dir, task, "nnUNet_Preprocessed")
        os.environ["nnUNet_results"] = os.path.join(root_nnunet_dir, task, "nnUNet_Results")

        
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
        task = "task2"  # Replace with the actual task name or pass it as a parameter
        root_nnunet_dir=r"D:\Liver Segmentation Meena 2024\monai_radiology_app\radiology\nnunet"
        os.environ["nnUNet_raw"] = os.path.join(root_nnunet_dir, task, "nnUNet_raw")
        os.environ["nnUNet_preprocessed"] = os.path.join(root_nnunet_dir, task, "nnUNet_Preprocessed")
        os.environ["nnUNet_results"] = os.path.join(root_nnunet_dir, task, "nnUNet_Results")

        
        preprocessed_data_path = os.path.join(os.environ["nnUNet_preprocessed"], self.task_name)
        # if os.path.exists(preprocessed_data_path):
        #     logger.info(f"Dataset already preprocessed: {preprocessed_data_path}")
        #     return preprocessed_data_path
  

        # Build the nnUNet preprocessing command
        command = [    # change
            "nnUNetv2_plan_and_preprocess",
            "-d", self.task_id,
            "--verify_dataset_integrity"
        ]


        logger.info(f"Running command: {' '.join(command)}")

        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Preprocessing failed with error: {e}")
            raise RuntimeError(f"Preprocessing failed for dataset {self.task_name}, error: {e}")


        logger.info(f"Preprocessed data saved to: {preprocessed_data_path}")
        return preprocessed_data_path

    def train(self, rank: int, world_size: int, request, datalist):
        # Train the model using the nnUNet CLI.
        logger.info("Starting nnUNet Training")

        # Ensure preprocessing is complete
        self.preprocess_dataset()

        task = "task2"  # Replace with the actual task name or pass it as a parameter
        root_nnunet_dir=r"D:\Liver Segmentation Meena 2024\monai_radiology_app\radiology\nnunet"
        os.environ["nnUNet_raw"] = os.path.join(root_nnunet_dir, task, "nnUNet_raw")
        os.environ["nnUNet_preprocessed"] = os.path.join(root_nnunet_dir, task, "nnUNet_Preprocessed")
        os.environ["nnUNet_results"] = os.path.join(root_nnunet_dir, task, "nnUNet_Results")


        # Run the nnUNet training command
        # Build the nnUNet training command
        command = [      # change
            "nnUNetv2_train",
             self.task_id, 
            "2d",  
             str(self.fold), 
            "-tr", self.trainer_class, 
        ]
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Training failed with error: {e}")
            raise RuntimeError(f"Training failed for dataset {self.task_name}, fold {self.fold}, error: {e}")

        logger.info("nnUNet Training Completed")

    def val_inferer(self, context: Context):
        # Inference logic for nnUNet.
        from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor

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
            os.path.join(nnUNet_results, self.task_name, self.trainer_class),
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


    
