from __future__ import annotations

import os

import httpx
import pytest


RUN_INGRESS = os.environ.get("RUN_INGRESS_INTEGRATION") == "1"
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://localhost")


@pytest.mark.integration
@pytest.mark.docker
@pytest.mark.skipif(
    not RUN_INGRESS,
    reason="Set RUN_INGRESS_INTEGRATION=1 after starting the frontend production stack.",
)
@pytest.mark.parametrize(
    "path",
    [
        "/api/health/live",
        "/agents/openapi.json",
        "/inference/info/",
        "/pacs/system",
    ],
)
def test_single_origin_http_routes(path: str):
    response = httpx.get(f"{FRONTEND_BASE_URL}{path}", timeout=30.0)
    assert response.status_code == 200
