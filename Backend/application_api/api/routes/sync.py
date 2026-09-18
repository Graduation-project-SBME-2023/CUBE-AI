import asyncio
import logging
import secrets

from pydantic import BaseModel
from fastapi import APIRouter, Depends, BackgroundTasks, Header, HTTPException
from application_api.db.session import get_db, AsyncSessionLocal
from application_api.core.config import settings
from sqlalchemy import func
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

# Arbitrary but fixed: identifies the full-sync lock among advisory locks.
_FULL_SYNC_LOCK_KEY = 0x5CB1A17C


async def _run_full_sync_once(db: AsyncSession) -> bool:
    """Run a full sync unless another worker is already running one.

    This API runs several uvicorn workers, and both the startup reconcile and
    the manual endpoint can fire in each of them at once. A full sync walks every
    study and upserts it, so two of them racing means duplicated work at best and
    rows deadlocking against each other at worst.

    A session-level advisory lock is the cheap fix: it is held in PostgreSQL, so
    it covers every worker, and it is released when this connection closes even
    if the process is killed mid-sync.
    """
    acquired = (
        await db.execute(select(func.pg_try_advisory_lock(_FULL_SYNC_LOCK_KEY)))
    ).scalar()
    if not acquired:
        logger.info("A full Orthanc sync is already running; skipping this one")
        return False
    try:
        await sync_all_from_orthanc(db)
        return True
    finally:
        await db.execute(select(func.pg_advisory_unlock(_FULL_SYNC_LOCK_KEY)))


async def run_full_sync_background():
    """Background task for full sync."""
    async with AsyncSessionLocal() as db:
        await _run_full_sync_once(db)


async def reconcile_with_orthanc(delay_seconds: float = 5.0) -> None:
    """Backfill anything in Orthanc that the database never heard about.

    The database only learns of a study through Orthanc's stable-series hook,
    which fires once, when the study arrives. Anything already in Orthanc before
    this API could receive that call -- a restore from the named volume, studies
    pushed while the API was down, a database recreated under an existing PACS --
    stays invisible forever, because nothing re-checks. The cohort browser reads
    the database, so those studies simply are not offered to a study population,
    with no error anywhere to explain why.

    A full sync is idempotent (every writer upserts), so the cheap correct fix
    is to compare counts and backfill only when they disagree. That keeps the
    common case -- already in sync -- down to one /statistics call and one
    COUNT, which is what makes this safe to run on a timer. Orthanc is
    authoritative; the database is a projection of it.

    Never raises: the API must serve requests whether or not Orthanc is
    reachable yet, and a caller on a timer must not die on one bad pass.
    """
    try:
        # Orthanc and this API usually start together, and its index is not
        # queryable the instant its port opens.
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        async with create_orthanc_session() as session:
            async with session.get(f"{ORTHANC_URL}/statistics") as response:
                if response.status != 200:
                    logger.warning(
                        "Skipping the Orthanc reconcile: statistics returned %s",
                        response.status,
                    )
                    return
                stats = await response.json()

        orthanc_studies = int(stats.get("CountStudies", 0))
        async with AsyncSessionLocal() as db:
            db_studies = (
                await db.execute(select(func.count()).select_from(Study))
            ).scalar() or 0

            if db_studies >= orthanc_studies:
                logger.info(
                    "Orthanc reconcile not needed: %s studies in the database, "
                    "%s in Orthanc",
                    db_studies,
                    orthanc_studies,
                )
                return

            logger.info(
                "Backfilling from Orthanc: the database has %s of %s studies",
                db_studies,
                orthanc_studies,
            )
            if await _run_full_sync_once(db):
                logger.info("Orthanc reconcile finished")
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - housekeeping is never fatal
        logger.exception("Orthanc reconcile failed; the database may be incomplete")


async def reconcile_with_orthanc_forever(interval_seconds: float) -> None:
    """Reconcile at startup and then on a timer.

    The webhook is fire-and-forget, so an event raised while this API is
    restarting is simply lost and nothing else re-derives the database from
    Orthanc's actual state. The first pass runs immediately rather than after a
    full interval: a database that starts out behind should not stay behind for
    the length of the timer.
    """
    await reconcile_with_orthanc()
    while True:
        await asyncio.sleep(interval_seconds)
        await reconcile_with_orthanc(delay_seconds=0)


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
