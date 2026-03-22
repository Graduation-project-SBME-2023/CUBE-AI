import logging
from pathlib import Path


def setup_logging():

    log_dir = Path(__file__).parent.parent.parent / "memory" / "workspace"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "segmentation_module.log"

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # console
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # file
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)