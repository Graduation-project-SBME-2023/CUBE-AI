from __future__ import annotations

import os

import httpx
import pytest


RUN_INTEGRATION = os.environ.get("RUN_BACKEND_INTEGRATION") == "1"
AGENTS_BASE_URL = os.environ.get("AGENTS_BASE_URL", "http://localhost:8150")
CONTROL_BASE_URL = os.environ.get("CONTROL_BASE_URL", "http://localhost:8001")
MONAI_BASE_URL = os.environ.get("MONAI_BASE_URL", "http://localhost:8000")
ORTHANC_BASE_URL = os.environ.get("ORTHANC_BASE_URL", "http://localhost:8043")


@pytest.mark.integration
@pytest.mark.docker
@pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="Set RUN_BACKEND_INTEGRATION=1 to contact deployed services.",
)
@pytest.mark.parametrize(
    ("url", "expected_status"),
    [
        (f"{AGENTS_BASE_URL}/openapi.json", 200),
        (f"{CONTROL_BASE_URL}/health", 200),
        (f"{MONAI_BASE_URL}/info/", 200),
        (f"{ORTHANC_BASE_URL}/system", 200),
    ],
)
def test_deployed_backend_services_are_healthy(url, expected_status):
    response = httpx.get(url, timeout=10.0)
    assert response.status_code == expected_status
