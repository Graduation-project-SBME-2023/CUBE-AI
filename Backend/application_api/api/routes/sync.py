import logging
import secrets

from pydantic import BaseModel
from fastapi import APIRouter, Depends, BackgroundTasks, Header, HTTPException
from application_api.db.session import get_db, AsyncSessionLocal
from application_api.core.config import settings
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from application_api.repositories.orthanc_sync import (
    sync_study_to_db,
    sync_series_to_db,
    sync_all_from_orthanc,
    delete_study_from_db,
    delete_series_from_db,
    delete_patient_from_db,
    delete_segmentation_from_db,
)
from application_api.services.orthanc_client import ORTHANC_URL, create_orthanc_session
from application_api.db.models.patient import Patient
from application_api.db.models.study import Study
from application_api.db.models.volume import Volume
from application_api.db.models.segmentation import Segmentation

router = APIRouter(prefix="/sync", tags=["orthanc"])
logger = logging.getLogger(__name__)


# ----------------- Webhook authentication (D-30) -----------------
# Orthanc's Lua hook is the only caller. If ORTHANC_WEBHOOK_TOKEN is set,
# require it on every webhook call so an unauthenticated request cannot
# delete rows by supplying an arbitrary resource_id. Set the same value in
# the Lua script's JSON_HEADERS (see Backend/orthanc/lua/on-stable-series.lua).
async def verify_webhook_token(x_webhook_token: str | None = Header(default=None)) -> None:
    if settings.ORTHANC_WEBHOOK_TOKEN is None:
        return
    if not x_webhook_token or not secrets.compare_digest(x_webhook_token, settings.ORTHANC_WEBHOOK_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing webhook token")


# ----------------- Pydantic Payload -----------------

class OrthancWebhookPayload(BaseModel):
    change_type: str
    resource_id: str
    level: str
    modality: str | None = None
    series_uid: str | None = None
    study_uid: str | None = None


# ----------------- MANUAL FULL SYNC -----------------

@router.post("/sync-all", dependencies=[Depends(verify_webhook_token)])
async def manual_full_sync(background_tasks: BackgroundTasks):
    """
    Manually trigger a full sync of all Orthanc data.
    Runs in the background and returns immediately.
    """
    try:
        async with create_orthanc_session() as session:
            async with session.get(f"{ORTHANC_URL}/statistics") as response:
                if response.status != 200:
                    raise HTTPException(status_code=503, detail="Orthanc is unavailable")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Orthanc is unavailable") from exc
    background_tasks.add_task(run_full_sync_background)
    return {
        "status": "accepted",
        "message": "Full sync started in background."
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
        async with create_orthanc_session() as session:
            async with session.get(f"{ORTHANC_URL}/statistics") as resp:
                orthanc_stats = await resp.json() if resp.status == 200 else {}

        db_series_count = (volumes_count or 0) + (segs_count or 0)
        orthanc_series_count = orthanc_stats.get("CountSeries")
        orthanc_studies_count = orthanc_stats.get("CountStudies")

        # Compare studies AND series (volumes + segmentations combined),
        # not just studies — a study-only comparison can read "synced" while
        # entire series are missing underneath it.
        synced = None
        if orthanc_stats:
            synced = (
                studies_count == orthanc_studies_count
                and db_series_count == orthanc_series_count
            )

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
            "synced": synced,
        }
    except Exception as exc:
        logger.warning("Unable to read Orthanc sync status: %s", exc)
        raise HTTPException(status_code=503, detail="Orthanc is unavailable") from exc


# ----------------- BACKGROUND TASKS -----------------

async def run_full_sync_background():
    """Background task for full sync."""
    async with AsyncSessionLocal() as db:
        await sync_all_from_orthanc(db)


async def process_webhook_background(change_type: str, resource_id: str, level: str):
    """Process webhook changes in background."""
    async with AsyncSessionLocal() as db:
        try:
            if change_type == "stable_study":
                await sync_study_to_db(db, orthanc_study_id=resource_id)
                await db.commit()

            elif change_type == "stable_series":
                await sync_series_to_db(db, orthanc_series_id=resource_id)
                await db.commit()

            # ---------- DELETE EVENTS ----------
            elif change_type == "deleted":
                level_lower = level.lower()
                
                # We need to fetch the DICOM UID BEFORE deletion happens
                # But the resource is already deleted in Orthanc, so we query our DB
                
                if level_lower == "patient":
                    # For patient, we use orthanc_patient_id directly
                    deleted = await delete_patient_from_db(db, resource_id)
                    if not deleted:
                        logger.warning("Patient %s not found in database", resource_id)

                elif level_lower == "study":
                    # Find study by orthanc_study_id and delete
                    result = await db.execute(
                        select(Study).where(Study.orthanc_study_id == resource_id)
                    )
                    study = result.scalar_one_or_none()
                    if study:
                        study_uid = study.study_uid
                        await delete_study_from_db(db, study_uid)
                    else:
                        logger.warning("Study with Orthanc ID %s not found in database", resource_id)

                elif level_lower == "series":
                    # Try to find as volume first
                    result = await db.execute(
                        select(Volume).where(Volume.orthanc_series_id == resource_id)
                    )
                    volume = result.scalar_one_or_none()
                    
                    if volume:
                        series_uid = volume.volume_series_instance_uid
                        await delete_series_from_db(db, series_uid)
                    else:
                        # Try as segmentation
                        result = await db.execute(
                            select(Segmentation).where(Segmentation.orthanc_series_id == resource_id)
                        )
                        seg = result.scalar_one_or_none()
                        if seg:
                            seg_uid = seg.label_series_instance_uid
                            await delete_segmentation_from_db(db, seg_uid)
                        else:
                            logger.warning("Series with Orthanc ID %s not found in database", resource_id)

                elif level_lower == "instance":
                    # Instance deletion: only delete if it's the last instance in a series
                    # Typically we do not delete a series on single-instance deletion.
                    pass

                else:
                    logger.warning("Unknown delete level: %s", level_lower)

                await db.commit()

            else:
                return

        except Exception as e:
            logger.error(
                "Background sync failed for %s %s: %s",
                change_type,
                resource_id,
                e,
                exc_info=True,
            )
            await db.rollback()


# ----------------- ORTHANC WEBHOOK -----------------

@router.post("/orthanc-webhook", dependencies=[Depends(verify_webhook_token)])
async def orthanc_webhook(payload: OrthancWebhookPayload, background_tasks: BackgroundTasks):
    """
    Webhook endpoint that responds immediately and processes in background.
    Supports sync + deletion.
    """
    supported_events = {"stable_study", "stable_series", "deleted"}

    if payload.change_type in supported_events:
        background_tasks.add_task(
            process_webhook_background,
            payload.change_type,
            payload.resource_id,
            payload.level
        )
        return {
            "status": "accepted",
            "message": f"Processing {payload.change_type} {payload.resource_id} in background"
        }

    return {
        "status": "ignored",
        "message": f"No action for change_type={payload.change_type}"
    }
