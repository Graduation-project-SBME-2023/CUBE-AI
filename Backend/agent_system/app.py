import os
import sys

# Make Backend/ importable (storage_paths lives there) before any local import.
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, ".."))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import asyncio
import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class RedactApiKeyFilter(logging.Filter):
    def filter(self, record):
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            record.msg = re.sub(r'\'api_key\': \'[^\']*\'', "'api_key': '***'", record.msg)
            record.msg = re.sub(r'"api_key": "[^"]*"', '"api_key": "***"', record.msg)
        return True

logging.getLogger().addFilter(RedactApiKeyFilter())

logging.getLogger("watchfiles").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

from storage_paths import ensure_storage_layout
from services.best_effort import drain_best_effort, initialize_best_effort
from services.mask_store import MaskStore
from ingestion.routes import router as ingestion_router
# session_service is re-exported: tool_logging.py and
# agents/execution_agents/segmentation/pipelines/common.py import it from app.
from api.runtime import agents_workspace_dir, cors_origins, session_service
from api.routes import (
    client_settings,
    events,
    execution,
    files,
    populations,
    reports,
    segmentation_review,
    sessions,
    websocket,
)

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# App setup
# ----------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_best_effort()
    # A worker killed mid-run leaves its mask cache behind. Sweeping on startup
    # keeps that from accumulating; the caches are disposable by definition, so
    # the worst case is a re-download.
    try:
        await asyncio.to_thread(MaskStore.sweep_stale_caches)
    except Exception:  # noqa: BLE001 - never block startup on housekeeping
        logger.exception("Mask cache sweep failed during startup")
    try:
        yield
    finally:
        await drain_best_effort(timeout_seconds=2.0)


app = FastAPI(
    title="MAS Orchestrator API",
    description="Receive a clinical hypothesis, run the Planner → Executor pipeline.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in cors_origins],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CoopCoepMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response


app.add_middleware(CoopCoepMiddleware)

# Volume-file ingestion lives in its own router rather than inline below.
app.include_router(ingestion_router)

ensure_storage_layout()
# A write to a retired workspace file raises from here on.
from storage_write_guard import install_write_guard

install_write_guard()

# Refuses to start if /events and /ws-broadcast would accept unauthenticated
# internal events -- see services/internal_auth.py and WEBSOCKET_FIX_PLAN.md W12.
from services.internal_auth import ensure_configured as ensure_internal_auth_configured

ensure_internal_auth_configured()

# The legacy workspace-file route must be registered before the /files mount,
# which would otherwise shadow it.
app.include_router(files.router)
app.mount(
    "/files",
    StaticFiles(directory=agents_workspace_dir),
    name="files"
)
for router in (
    execution.router,
    events.router,
    client_settings.router,
    sessions.router,
    reports.router,
    websocket.router,
    populations.router,
    segmentation_review.router,
):
    app.include_router(router)


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    from startup_logging import uvicorn_log_config

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8150,
        reload=True,
        log_config=uvicorn_log_config("Agents API"),
    )
