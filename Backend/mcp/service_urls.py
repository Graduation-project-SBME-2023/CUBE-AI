import os


INFERENCE_API_URL = os.environ.get(
    "INFERENCE_API_URL",
    os.environ.get("MONAI_URL", "http://localhost:8000"),
).rstrip("/")
APP_API_URL = os.environ.get(
    "APP_API_URL",
    os.environ.get("CONTROL_API_URL", "http://localhost:8001"),
).rstrip("/")

# Compatibility aliases; application traffic never falls back to inference.
MONAI_URL = INFERENCE_API_URL
CONTROL_API_URL = APP_API_URL
