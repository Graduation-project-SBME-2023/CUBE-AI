from monailabel.app import app as monai_server_app
from contextlib import asynccontextmanager
from seeders.all_seeders import all_seeders
from crud.orthanc_sync import sync_all_from_orthanc
from core.database import AsyncSessionLocal
import asyncio
from endpoints import (
    hello_world,
    create_model,
    workflow,
    norm_align,
    new_handle,
    my_endpoints_datastore,
    patient,
    study,
    volume,
    model,
    segmentation,
    radiomics,
    chat_sessions,
    chat_messages,
    sync,
    hypothesis,
    plan,   
    steps,
    Taskmessage,
    contextaugmentation,
        Wslog

    
    

)

monai_server_app.include_router(workflow.router)
monai_server_app.include_router(create_model.router)
monai_server_app.include_router(new_handle.router)
monai_server_app.include_router(norm_align.router)
monai_server_app.include_router(hello_world.router)
monai_server_app.include_router(my_endpoints_datastore.router)
monai_server_app.include_router(patient.router)
monai_server_app.include_router(study.router)
monai_server_app.include_router(volume.router)
monai_server_app.include_router(model.router)
monai_server_app.include_router(segmentation.router)
monai_server_app.include_router(radiomics.router)
monai_server_app.include_router(chat_sessions.router)
monai_server_app.include_router(chat_messages.router)
monai_server_app.include_router(sync.router)
monai_server_app.include_router(hypothesis.router)
monai_server_app.include_router(plan.router)
monai_server_app.include_router(steps.router)
monai_server_app.include_router(Taskmessage.router)
monai_server_app.include_router(contextaugmentation.router)
monai_server_app.include_router(Wslog.router)


original_lifespan = monai_server_app.router.lifespan_context

@asynccontextmanager
async def lifespan(app):
    print("[INFO] 🧹 Running database seeders...")
    all_seeders()  # sync seeders
    

    # print("[INFO] 🚀 Running initial Orthanc sync...")
    # async with AsyncSessionLocal() as db:
    #     try:
    #         await sync_all_from_orthanc(db)
    #     except Exception as e:
    #         print(f"[ERROR] Initial Orthanc sync failed: {e}")

    if original_lifespan:
        async with original_lifespan(app):
            print("[INFO] 🧹 Running original lifespan tasks...")
            yield
    else:
        print("[INFO] 🧹 No original lifespan tasks found, skipping...")
        yield
        
    print("[INFO] 👋 Shutting down")

# attach lifespan to app
monai_server_app.router.lifespan_context = lifespan