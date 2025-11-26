from pydantic import BaseModel
from fastapi import APIRouter, Depends, BackgroundTasks
from core.database import get_db, AsyncSessionLocal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import aiohttp

from crud.orthanc_sync import (
    sync_study_to_db,
    sync_series_to_db,
    sync_all_from_orthanc,
    AUTH,
    ORTHANC_URL
)
from models.patient import Patient
from models.study import Study
from models.volume import Volume
from models.segmentation import Segmentation

router = APIRouter(prefix="/sync", tags=["orthanc"])


# ----------------- Pydantic Payload -----------------

class OrthancWebhookPayload(BaseModel):
    change_type: str
    resource_id: str
    level: str
    modality: str | None = None
    series_uid: str | None = None
    study_uid: str | None = None


# ----------------- MANUAL FULL SYNC -----------------

@router.post("/sync-all")
async def manual_full_sync(background_tasks: BackgroundTasks):
    """
    Manually trigger a full sync of all Orthanc data.
    Runs in the background and returns immediately.
    """
    print("[INFO] Manual full sync requested")
    background_tasks.add_task(run_full_sync_background)
    return {
        "status": "accepted",
        "message": "Full sync started in background. Check logs for progress."
    }


@router.get("/sync-status")
async def get_sync_status(db: AsyncSession = Depends(get_db)):
    """
    Get current sync status: count of patients, studies, volumes, and segmentations.
    """
    try:
        from sqlalchemy import func

        # Count records in database
        patients_count = (await db.execute(select(func.count()).select_from(Patient))).scalar()
        studies_count = (await db.execute(select(func.count()).select_from(Study))).scalar()
        volumes_count = (await db.execute(select(func.count()).select_from(Volume))).scalar()
        segs_count = (await db.execute(select(func.count()).select_from(Segmentation))).scalar()

        # Get counts from Orthanc
        async with aiohttp.ClientSession(auth=AUTH) as session:
            async with session.get(f"{ORTHANC_URL}/statistics") as resp:
                orthanc_stats = await resp.json() if resp.status == 200 else {}

        return {
            "database": {
                "patients": patients_count or 0,
                "studies": studies_count or 0,
                "volumes": volumes_count or 0,
                "segmentations": segs_count or 0
            },
            "orthanc": {
                "patients": orthanc_stats.get("CountPatients", "unknown"),
                "studies": orthanc_stats.get("CountStudies", "unknown"),
                "series": orthanc_stats.get("CountSeries", "unknown"),
                "instances": orthanc_stats.get("CountInstances", "unknown")
            },
            "synced": studies_count == orthanc_stats.get("CountStudies", 0) if orthanc_stats else None
        }
    except Exception as e:
        return {
            "error": str(e),
            "message": "Failed to get sync status"
        }


# ----------------- BACKGROUND TASKS -----------------

async def run_full_sync_background():
    """Background task for full sync"""
    async with AsyncSessionLocal() as db:
        await sync_all_from_orthanc(db)


async def process_webhook_background(change_type: str, resource_id: str):
    """Process webhook changes in background"""
    print(f"[INFO] Background processing started for {change_type}: {resource_id}")

    async with AsyncSessionLocal() as db:
        try:
            if change_type == "stable_study":
                await sync_study_to_db(db, resource_id)
                await db.commit()
                print(f"[INFO] ✅ Background sync completed for study {resource_id}")

            elif change_type == "stable_series":
                await sync_series_to_db(db, resource_id)
                await db.commit()
                print(f"[INFO] ✅ Background sync completed for series {resource_id}")

        except Exception as e:
            print(f"[ERROR] ❌ Background sync failed for {change_type} {resource_id}: {e}")
            import traceback
            traceback.print_exc()
            await db.rollback()


# ----------------- ORTHANC WEBHOOK -----------------

@router.post("/orthanc-webhook")
async def orthanc_webhook(payload: OrthancWebhookPayload, background_tasks: BackgroundTasks):
    """
    Webhook endpoint that responds immediately and processes in background.
    Supports hybrid incremental sync.
    """
    print(f"[INFO] Received webhook: {payload.change_type} for resource {payload.resource_id}")

    if payload.change_type in ["stable_study", "stable_series"]:
        # Add task to background queue and return immediately
        background_tasks.add_task(
            process_webhook_background,
            payload.change_type,
            payload.resource_id
        )
        return {
            "status": "accepted",
            "message": f"Processing {payload.change_type} {payload.resource_id} in background"
        }

    # Ignore other change types
    return {
        "status": "ignored",
        "message": f"No action for change_type={payload.change_type}"
    }
