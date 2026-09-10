import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from application_api.db.session import AsyncSessionLocal, engine
from application_api.core.config import settings
from application_api.core.metrics import install_sql_metrics, monitor_event_loop_lag, observe_http, render_metrics
from application_api.repositories.orthanc_sync import sync_all_from_orthanc
from application_api.api.routes import (
    Taskmessage,
    Wslog,
    chat_messages,
    chat_sessions,
    contextaugmentation,
    hypothesis,
    model,
    patient,
    plan,
    radiomics,
    segmentation,
    steps,
    study,
    sync,
    volume,
    workflow,
)

logger = logging.getLogger(__name__)

install_sql_metrics(engine)


async def _periodic_reconciliation(interval_seconds: float) -> None:
    """Run sync_all_from_orthanc on a timer.

    This is the reconciliation backstop for the Lua webhook (D-11/D-18):
    the webhook is fire-and-forget and can lose an event if application-api
    is restarting when it fires, and nothing else in the system ever
    re-derives the database from Orthanc's actual state. sync_all_from_orthanc
    is idempotent, so running it repeatedly is safe.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with AsyncSessionLocal() as db:
                await sync_all_from_orthanc(db)
        except Exception:
            logger.exception("Periodic Orthanc reconciliation pass failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    lag_task = asyncio.create_task(monitor_event_loop_lag(), name="event-loop-lag-monitor")
    reconciliation_task = asyncio.create_task(
        _periodic_reconciliation(settings.RECONCILIATION_INTERVAL_SECONDS),
        name="orthanc-reconciliation",
    )
    try:
        yield
    finally:
        lag_task.cancel()
        reconciliation_task.cancel()
        with suppress(asyncio.CancelledError):
            await lag_task
        with suppress(asyncio.CancelledError):
            await reconciliation_task
        await engine.dispose()


app = FastAPI(title="CUBE.AI Application API", version="1.1.0", lifespan=lifespan)
_cors_origins = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (
    patient.router,
    study.router,
    volume.router,
    model.router,
    segmentation.router,
    radiomics.router,
    chat_sessions.router,
    chat_messages.router,
    hypothesis.router,
    plan.router,
    steps.router,
    Taskmessage.router,
    contextaugmentation.router,
    Wslog.router,
    workflow.router,
    sync.router,
):
    app.include_router(router)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    started = time.perf_counter()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        observe_http(
            request.method,
            route_path,
            response.status_code if response is not None else 500,
            time.perf_counter() - started,
        )


@app.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
async def readiness() -> dict[str, str]:
    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))
        # alembic_version holds one row per head; .scalar_one() would raise
        # as soon as the chain branches without a merge revision in place.
        revisions = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalars().all()
    return {"status": "ready", "migration": ",".join(sorted(revisions))}


@app.get("/health")
async def health() -> dict[str, str]:
    await readiness()
    return {"status": "healthy"}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(render_metrics(engine), media_type="text/plain; version=0.0.4")
