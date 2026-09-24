"""Process-wide singletons and helpers shared by the Agent System routers.

Import-time side effects (ensure_storage_layout, install_write_guard) stay in
app.py so they keep running after every module has been imported.
"""

import os

from fastapi import HTTPException

from storage_paths import HYPOTHESES_DIR
from socket_store import manager as ws_manager
from services import population_review
from services.session_service import SessionService

cors_origins = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:8150"
).split(",")

agents_workspace_dir = str(HYPOTHESES_DIR)

# Single shared service — the only stateful object routes need to know about
session_service = SessionService(ws_manager=ws_manager)


def domain_http_error(exc: "population_review.DomainError") -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)
