import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from application_api.db.binary_session import binary_engine
from application_api.db.session import AsyncSessionLocal, engine
from application_api.core.config import settings
from application_api.core.metrics import install_sql_metrics, monitor_event_loop_lag, observe_http, render_metrics
from application_api.api.routes import (
    chat_messages,
    chats,
    cohorts,
    feature_sets,
    graph_nodes,
    inference_models,
    literature,
    model_results,
    node_runs,
    patient,
    radiomics,
    reports,
    segmentation_batches,
    segmentation_masks,
    session_events,
    session_graphs,
    sessions,
    stored_files,
    study,
    sync,
    volume,
    workflow,
)

logger = logging.getLogger(__name__)

install_sql_metrics(engine)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    lag_task = asyncio.create_task(monitor_event_loop_lag(), name="event-loop-lag-monitor")
    # Orthanc is the source of truth for imaging. This catches up on anything
    # that arrived while the API was down or predates the database entirely,
    # then keeps watching for the events the fire-and-forget webhook drops.
    reconcile_task = asyncio.create_task(
        sync.reconcile_with_orthanc_forever(settings.RECONCILIATION_INTERVAL_SECONDS),
        name="orthanc-reconciliation",
    )
    try:
        yield
    finally:
        for task in (lag_task, reconcile_task):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await engine.dispose()
        await binary_engine.dispose()


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
    inference_models.router,
    segmentation_masks.router,
    radiomics.router,
    sessions.router,
    chats.router,
    chat_messages.router,
    session_graphs.router,
    graph_nodes.router,
    node_runs.router,
    stored_files.router,
    cohorts.router,
    segmentation_batches.router,
    feature_sets.router,
    model_results.router,
    reports.router,
    literature.router,
    session_events.router,
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
