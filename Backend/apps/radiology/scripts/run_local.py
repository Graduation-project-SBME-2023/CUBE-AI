"""Run a radiology task locally without starting the MONAI Label server."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

# Make the script directly executable from a source checkout. Container images
# already provide these paths through PYTHONPATH.
APP_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = APP_DIR.parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.append(str(BACKEND_DIR))

from monailabel.utils.others.generic import device_list, file_ext  # noqa: E402
from storage_paths import MEDICAL_IMAGES_DIR  # noqa: E402

from radiology_app import get_app_class  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> None:
    os.environ.setdefault("MASTER_ADDR", "127.0.0.1")
    os.environ.setdefault("MASTER_PORT", "1234")
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "[%(asctime)s] [%(process)s] [%(threadName)s] "
            "[%(levelname)s] (%(name)s:%(lineno)d) - %(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--studies", default=str(MEDICAL_IMAGES_DIR))
    parser.add_argument("-m", "--model", default="segmentation_spleen")
    parser.add_argument(
        "-t",
        "--test",
        default="batch_infer",
        choices=("train", "infer", "batch_infer"),
    )
    args = parser.parse_args()

    app = get_app_class()(
        str(APP_DIR),
        args.studies,
        {"models": args.model, "preload": "false"},
    )

    if args.test == "infer":
        sample = app.next_sample(request={"strategy": "first"})
        image_id = sample["id"]
        image_path = sample["path"]
        for device in device_list():
            result = app.infer(
                request={"model": args.model, "image": image_id, "device": device}
            )
            test_dir = os.path.join(args.studies, "test_labels")
            os.makedirs(test_dir, exist_ok=True)
            label_file = os.path.join(test_dir, image_id + file_ext(image_path))
            shutil.move(result["file"], label_file)
            logger.info("Inference metadata: %s", result["params"])
            logger.info("Image file: %s", image_path)
            logger.info("Label file: %s", label_file)
            break
        return

    if args.test == "batch_infer":
        app.batch_infer(
            request={
                "model": args.model,
                "multi_gpu": False,
                "save_label": True,
                "label_tag": "original",
                "max_workers": 1,
                "max_batch_size": 0,
            }
        )
        return

    app.train(
        request={
            "model": args.model,
            "max_epochs": 10,
            "dataset": "Dataset",
            "train_batch_size": 1,
            "val_batch_size": 1,
            "multi_gpu": False,
            "val_split": 0.1,
        }
    )


if __name__ == "__main__":
    main()
