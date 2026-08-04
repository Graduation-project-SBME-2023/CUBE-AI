from fastapi import FastAPI
from sqlalchemy import text

from core.database import AsyncSessionLocal
from endpoints import (
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
    volume,
)


control_app = FastAPI(title="CUBE.AI Control API", version="1.0.0")

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
):
    control_app.include_router(router)


@control_app.get("/health")
async def health() -> dict[str, str]:
    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "healthy"}
