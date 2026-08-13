"""Container entrypoint for pre-building the VoxTell text embedding cache."""

import logging
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = APP_DIR.parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.append(str(BACKEND_DIR))

from radiology_app.integrations.voxtell import build_voxtell_cache  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    build_voxtell_cache()


if __name__ == "__main__":
    main()
