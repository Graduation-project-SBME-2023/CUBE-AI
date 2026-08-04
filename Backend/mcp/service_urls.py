import os


MONAI_URL = os.environ.get("MONAI_URL", "http://localhost:8000").rstrip("/")
CONTROL_API_URL = os.environ.get("CONTROL_API_URL", MONAI_URL).rstrip("/")
