import torch
from nnunetv2.training.nnUNetTrainer.nnUNetTrainer import nnUNetTrainer

class CustomNnUnetTrainer(nnUNetTrainer):
    """
    Uses default nnUNet loss but improved optimizer and learning rate scheduling.
    """

    def __init__(self, plans, configuration, fold, dataset_json,
                 device=torch.device('cuda:1')):
        super().__init__(plans, configuration, fold, dataset_json, device=device)
        self.num_epochs = 5           # training length


    def initialize(self):
        """
        Initialize network, dataloaders, and default nnUNet loss (e.g. DiceCE)
        """
        super().initialize()
        self.print_to_log_file("Using default nnUNet loss with improved optimizer and LR scheduler.")

    def on_epoch_end(self):
        """
        Add custom logging at end of each epoch.
        """
        super().on_epoch_end()
        self.print_to_log_file(f"Epoch finished")
