from contextlib import asynccontextmanager

from monailabel.app import app as monai_server_app

from endpoints import (
    Taskmessage,
    Wslog,
    chat_messages,
    chat_sessions,
    contextaugmentation,
    create_model,
    hello_world,
    hypothesis,
    model,
    my_endpoints_datastore,
    new_handle,
    norm_align,
    patient,
    plan,
    populations,
    radiomics,
    segmentation,
    steps,
    study,
    sync,
    volume,
    workflow,
)
from seeders.all_seeders import all_seeders

for router in (
    workflow.router,
    create_model.router,
    new_handle.router,
    norm_align.router,
    hello_world.router,
    my_endpoints_datastore.router,
    patient.router,
    study.router,
    volume.router,
    model.router,
    segmentation.router,
    radiomics.router,
    chat_sessions.router,
    chat_messages.router,
    sync.router,
    hypothesis.router,
    plan.router,
    steps.router,
    Taskmessage.router,
    contextaugmentation.router,
    Wslog.router,
    populations.router,
):
    monai_server_app.include_router(router)


original_lifespan = monai_server_app.router.lifespan_context


@asynccontextmanager
async def lifespan(app):
    all_seeders()

    if original_lifespan:
        async with original_lifespan(app):
            yield
    else:
        yield


monai_server_app.router.lifespan_context = lifespan
