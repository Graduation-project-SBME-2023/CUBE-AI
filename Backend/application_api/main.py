import asyncio
import os
import time
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from application_api.db.session import AsyncSessionLocal, engine
from application_api.core.metrics import install_sql_metrics, monitor_event_loop_lag, observe_http, render_metrics
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


install_sql_metrics(engine)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    lag_task = asyncio.create_task(monitor_event_loop_lag(), name="event-loop-lag-monitor")
    try:
        yield
    finally:
        lag_task.cancel()
        with suppress(asyncio.CancelledError):
            await lag_task
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
        revision = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    return {"status": "ready", "migration": revision}


@app.get("/health")
async def health() -> dict[str, str]:
    await readiness()
    return {"status": "healthy"}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(render_metrics(engine), media_type="text/plain; version=0.0.4")
