# ===============================
# orthanc_sync.py - Incremental Orthanc to database sync
# ===============================
import logging
import zlib
from datetime import datetime, timezone

import aiohttp
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from application_api.db.models.patient import Patient
from application_api.db.models.study import Study
from application_api.db.models.volume import Volume
from application_api.db.models.segmentation import Segmentation
from application_api.services.orthanc_client import ORTHANC_URL, create_orthanc_session

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# HELPER FUNCTIONS
# ----------------------------------------------------------------------

def _parse_dicom_datetime(date_str: str, time_str: str) -> datetime | None:
    """Combine DICOM date/time strings like '20231130' + '141744.0'.

    Returns None (rather than "now") when the value is missing or
    unparseable, so a malformed StudyDate is visible as NULL instead of
    being silently recorded as the current instant.
    """
    if not date_str:
        return None
    try:
        if not time_str:
            return datetime.strptime(date_str, "%Y%m%d")
        clean_time = time_str.split(".")[0].ljust(6, "0")  # remove decimals, pad to HHMMSS
        return datetime.strptime(f"{date_str}{clean_time}", "%Y%m%d%H%M%S")
    except Exception:
        logger.warning("Unparseable DICOM datetime: date=%r time=%r", date_str, time_str)
        return None


def _advisory_lock_key(*parts: str) -> int:
    """Deterministic 32-bit key for pg_advisory_xact_lock from string parts.

    Postgres advisory locks take a bigint; CRC32 keeps this well within the
    signed 32-bit range Postgres accepts for the single-key overload.
    """
    joined = "|".join(p or "" for p in parts)
    return zlib.crc32(joined.encode("utf-8")) & 0x7FFFFFFF


# ----------------------------------------------------------------------
# GET-OR-CREATE HELPERS
#
# All four helpers use INSERT ... ON CONFLICT DO NOTHING instead of
# insert-then-catch-IntegrityError. This avoids the old failure mode where
# catching a duplicate-key error forced a full `db.rollback()` that
# discarded every other pending object in the same sync unit (e.g. a
# Patient flushed moments earlier while syncing its Study). ON CONFLICT
# resolves the race at the statement level, inside the same transaction,
# with nothing to roll back.
# ----------------------------------------------------------------------

async def _get_or_create_patient(
    db: AsyncSession,
    orthanc_patient_id: str,
    name: str,
    birth_date: str,
    gender: str,
) -> Patient:
    """Find or create a Patient record. Preserves manual edits on existing records."""
    name = name or "Unknown"
    if gender not in ("M", "F"):
        if gender:
            logger.info("Dropping unrecognized PatientSex value %r for patient %s", gender, orthanc_patient_id)
        gender = None

    try:
        bdate = datetime.strptime(birth_date, "%Y%m%d").date() if birth_date else None
    except Exception:
        logger.warning("Unparseable PatientBirthDate %r for patient %s", birth_date, orthanc_patient_id)
        bdate = None

    stmt = (
        pg_insert(Patient)
        .values(
            name=name,
            orthanc_patient_id=orthanc_patient_id,
            birth_date=bdate,
            gender=gender,
            created_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_nothing(index_elements=["orthanc_patient_id"])
    )
    await db.execute(stmt)

    result = await db.execute(
        select(Patient).where(Patient.orthanc_patient_id == orthanc_patient_id)
    )
    patient = result.scalar_one()

    # Only fill missing gender; do not overwrite manual edits.
    if gender and not patient.gender:
        patient.gender = gender
        db.add(patient)
    return patient


async def _get_or_create_study(
    db: AsyncSession,
    study_uid: str,
    orthanc_study_id: str,
    patient_id: int,
    description: str,
    modality: str,
    created_at: datetime,
) -> Study:
    """Find or create a Study record.

    DICOM-sourced fields (description, modality) are refreshed from Orthanc
    on every sync rather than left write-once, so a corrected DICOM
    re-sent to Orthanc actually updates the mirrored row.
    """
    stmt = (
        pg_insert(Study)
        .values(
            study_uid=study_uid,
            orthanc_study_id=orthanc_study_id,
            patient_id=patient_id,
            description=description or "Unknown",
            modality=(modality or "UNK")[:20],
            created_at=created_at,
        )
        .on_conflict_do_nothing(index_elements=["study_uid"])
    )
    await db.execute(stmt)

    result = await db.execute(select(Study).where(Study.study_uid == study_uid))
    study = result.scalar_one()

    updated = False
    if not study.orthanc_study_id:
        study.orthanc_study_id = orthanc_study_id
        updated = True
    if description and study.description != description:
        study.description = description
        updated = True
    if modality and study.modality != modality[:20]:
        study.modality = modality[:20]
        updated = True
    if updated:
        db.add(study)
    return study


async def _get_or_create_volume(
    db: AsyncSession,
    volume_uid: str,
    orthanc_series_id: str,
    study_uid: str,
    desc: str,
    modality: str,
    num_slices: int,
    created_at: datetime,
    body_part_examined: str = None,
) -> Volume:
    """Find or create a Volume (non-SEG series) record.

    DICOM-sourced fields are refreshed from Orthanc on every sync (see
    _get_or_create_study). num_slices is clamped to at least 1 to satisfy
    the DB's CHECK(num_slices > 0); the clamp is logged so a zero-instance
    series is visible instead of silently reported as having one slice.
    """
    if num_slices < 1:
        logger.warning(
            "Series %s (volume %s) reported %d instances; recording as 1 "
            "to satisfy the num_slices > 0 constraint.",
            orthanc_series_id, volume_uid, num_slices,
        )
    safe_num_slices = max(1, num_slices)

    stmt = (
        pg_insert(Volume)
        .values(
            volume_series_instance_uid=volume_uid,
            orthanc_series_id=orthanc_series_id,
            study_uid=study_uid,
            series_description=desc or "Unknown",
            modality=modality or "UNK",
            num_slices=safe_num_slices,
            created_at=created_at,
            body_part_examined=body_part_examined,
        )
        .on_conflict_do_nothing(index_elements=["volume_series_instance_uid"])
    )
    await db.execute(stmt)

    result = await db.execute(
        select(Volume).where(Volume.volume_series_instance_uid == volume_uid)
    )
    volume = result.scalar_one()

    updated = False
    if not volume.orthanc_series_id:
        volume.orthanc_series_id = orthanc_series_id
        updated = True
    if desc and volume.series_description != desc:
        volume.series_description = desc
        updated = True
    if modality and volume.modality != modality:
        volume.modality = modality
        updated = True
    if body_part_examined and volume.body_part_examined != body_part_examined:
        volume.body_part_examined = body_part_examined
        updated = True
    if safe_num_slices and volume.num_slices != safe_num_slices:
        volume.num_slices = safe_num_slices
        updated = True
    if updated:
        db.add(volume)
    return volume


async def _get_or_create_segmentation(
    db: AsyncSession,
    label_uid: str,
    orthanc_series_id: str,
    volume_uid: str,
    segmentation_type: str,
    target_label: str | None,
    method: str,
    created_at: datetime = None,
) -> Segmentation | None:
    """Find or create a Segmentation record. Returns None if the referenced volume is missing.

    A SEG whose parent volume has not synced in yet is not retried
    immediately here (see sync_series_to_db, which resolves the parent
    series first when possible); it is instead picked up automatically by
    the periodic full reconciliation (sync_all_from_orthanc, wired into the
    application lifespan) the next time that runs, since re-syncing an
    already-synced volume and a not-yet-synced SEG is idempotent.
    """
    created_at = created_at or datetime.now(timezone.utc)

    if not volume_uid:
        logger.warning("Skipping SEG %s: No referenced volume UID found.", label_uid)
        return None

    v_result = await db.execute(
        select(Volume).where(Volume.volume_series_instance_uid == volume_uid)
    )
    if not v_result.scalar_one_or_none():
        logger.warning(
            "Deferring SEG %s: referenced volume %s not yet in DB. Will be "
            "picked up by the next full reconciliation pass.",
            label_uid, volume_uid,
        )
        return None

    stmt = (
        pg_insert(Segmentation)
        .values(
            label_series_instance_uid=label_uid,
            orthanc_series_id=orthanc_series_id,
            volume_series_instance_uid=volume_uid,
            segmentation_type=segmentation_type,
            target_label=target_label,
            method=method,
            name=None,
            created_at=created_at,
        )
        .on_conflict_do_nothing(index_elements=["label_series_instance_uid"])
    )
    await db.execute(stmt)

    result = await db.execute(
        select(Segmentation).where(Segmentation.label_series_instance_uid == label_uid)
    )
    seg = result.scalar_one()

    updated = False
    if not seg.orthanc_series_id:
        seg.orthanc_series_id = orthanc_series_id
        updated = True
    if segmentation_type and seg.segmentation_type != segmentation_type:
        seg.segmentation_type = segmentation_type
        updated = True
    if target_label and seg.target_label != target_label:
        seg.target_label = target_label
        updated = True
    if method and seg.method != method:
        seg.method = method
        updated = True
    if updated:
        db.add(seg)
    return seg


# ----------------------------------------------------------------------
# EXTRACT REFERENCED SERIES UID FOR SEG
# ----------------------------------------------------------------------

async def _fetch_first_instance_tags(
    session: aiohttp.ClientSession, series_info: dict
) -> dict | None:
    """Fetch the DICOM tags of a series' first instance, once.

    Both _extract_referenced_series_uid and _extract_segment_label used to
    issue their own /instances/{id}/tags request against the same instance;
    this shares one fetch between them.
    """
    instances = series_info.get("Instances", [])
    if not instances:
        return None
    try:
        async with session.get(f"{ORTHANC_URL}/instances/{instances[0]}/tags") as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception as e:
        logger.error("Failed fetching instance tags: %s", e)
        return None


def _extract_referenced_series_uid_from_tags(tags: dict | None) -> str | None:
    if not tags:
        return None
    ref_series_tag = tags.get("0008,1115")
    if ref_series_tag and ref_series_tag.get("Type") == "Sequence":
        seq = ref_series_tag.get("Value", [])
        if seq and "0020,000e" in seq[0]:
            return seq[0]["0020,000e"]["Value"]
    return None


def _extract_segment_label_from_tags(tags: dict | None) -> str | None:
    """Return the first DICOM SegmentLabel (0062,0005) for a SEG series.

    The application writes one target per DICOM-SEG series.  A missing value is
    intentionally returned as ``None`` so legacy records remain distinguishable
    from records with structured label metadata.
    """
    if not tags:
        return None
    segment_sequence = tags.get("0062,0002")
    if not segment_sequence or segment_sequence.get("Type") != "Sequence":
        return None
    sequence_items = segment_sequence.get("Value", [])
    if not sequence_items:
        return None
    segment_label = sequence_items[0].get("0062,0005")
    if isinstance(segment_label, dict):
        value = segment_label.get("Value")
    else:
        value = segment_label
    value = str(value or "").strip()
    return value or None


# ----------------------------------------------------------------------
# FULL SYNC
# ----------------------------------------------------------------------

async def sync_all_from_orthanc(db: AsyncSession) -> None:
    """Sync all existing studies from Orthanc.

    This is the reconciliation backstop: it is safe (idempotent) to call
    repeatedly, and re-syncing every study/series also retries any SEG
    that was previously deferred because its parent volume had not synced
    in yet. It is invoked both on demand (POST /sync/sync-all) and on a
    timer from the application lifespan (see main.py) so reconciliation
    happens even if nobody calls the endpoint.
    """
    async with create_orthanc_session() as session:
        try:
            async with session.get(f"{ORTHANC_URL}/studies") as resp:
                if resp.status != 200:
                    logger.error("Failed to fetch studies from Orthanc: HTTP %s", resp.status)
                    return
                study_ids = await resp.json()

            for study_id in study_ids:
                try:
                    await sync_study_to_db(db, study_id, session=session)
                    await db.commit()
                except Exception as e:
                    logger.error("Failed to sync study %s: %s", study_id, e)
                    await db.rollback()
        except Exception as e:
            logger.error("Initial sync failed: %s", e, exc_info=True)


# ----------------------------------------------------------------------
# SYNC STUDY & SERIES
# ----------------------------------------------------------------------

async def sync_study_to_db(
    db: AsyncSession,
    orthanc_study_id: str,
    study_uid: str = None,
    session: aiohttp.ClientSession = None,
) -> None:
    """Sync a single study and all its series from Orthanc to the database.

    Takes a per-study Postgres advisory lock for the duration of the
    transaction so that a concurrent sync of the same study (e.g. the
    Lua webhook's stable_study and stable_series firing close together,
    possibly in different worker processes) serializes instead of racing.
    The lock is transaction-scoped and releases automatically at commit
    or rollback.
    """
    own_session = session is None
    if own_session:
        session = create_orthanc_session()
    try:
        async with session.get(f"{ORTHANC_URL}/studies/{orthanc_study_id}") as resp:
            if resp.status != 200:
                logger.warning("Study %s not found in Orthanc.", orthanc_study_id)
                return
            study_info = await resp.json()

        tags = study_info.get("MainDicomTags", {})
        if study_uid is None:
            study_uid = tags.get("StudyInstanceUID")

        lock_key = _advisory_lock_key("study", study_uid or orthanc_study_id)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

        patient_orthanc_id = study_info.get("ParentPatient")
        async with session.get(f"{ORTHANC_URL}/patients/{patient_orthanc_id}") as resp:
            if resp.status != 200:
                logger.warning(
                    "Parent patient %s not found in Orthanc for study %s; skipping.",
                    patient_orthanc_id, orthanc_study_id,
                )
                return
            patient_info = await resp.json()
        p_tags = patient_info.get("MainDicomTags", {})

        patient = await _get_or_create_patient(
            db=db,
            orthanc_patient_id=patient_orthanc_id,
            name=p_tags.get("PatientName", "Unknown"),
            birth_date=p_tags.get("PatientBirthDate"),
            gender=p_tags.get("PatientSex"),
        )

        await _get_or_create_study(
            db=db,
            study_uid=study_uid,
            orthanc_study_id=orthanc_study_id,
            patient_id=patient.patient_id,
            description=tags.get("StudyDescription"),
            modality=tags.get("ModalitiesInStudy"),
            created_at=_parse_dicom_datetime(tags.get("StudyDate"), tags.get("StudyTime"))
            or datetime.now(timezone.utc),
        )

        for series_id in study_info.get("Series", []):
            await sync_series_to_db(db, series_id, session=session)
    finally:
        if own_session:
            await session.close()


async def sync_series_to_db(
    db: AsyncSession,
    orthanc_series_id: str,
    session: aiohttp.ClientSession = None,
) -> None:
    """Sync a single series (volume or SEG) from Orthanc to the database."""
    own_session = session is None
    if own_session:
        session = create_orthanc_session()
    try:
        async with session.get(f"{ORTHANC_URL}/series/{orthanc_series_id}") as resp:
            if resp.status != 200:
                logger.warning("Series %s not found in Orthanc.", orthanc_series_id)
                return
            series_info = await resp.json()

        s_tags = series_info.get("MainDicomTags", {})
        dicom_series_uid = s_tags.get("SeriesInstanceUID")
        modality = (s_tags.get("Modality") or "").upper()
        desc = s_tags.get("SeriesDescription")
        num_instances = len(series_info.get("Instances", []))
        body_part = s_tags.get("BodyPartExamined")
        series_datetime = _parse_dicom_datetime(
            s_tags.get("SeriesDate"), s_tags.get("SeriesTime")
        ) or datetime.now(timezone.utc)

        parent_study_id = series_info.get("ParentStudy")
        async with session.get(f"{ORTHANC_URL}/studies/{parent_study_id}") as resp:
            if resp.status != 200:
                logger.warning("Parent study %s not found in Orthanc.", parent_study_id)
                return
            study_info = await resp.json()

        dicom_study_uid = study_info.get("MainDicomTags", {}).get("StudyInstanceUID")

        lock_key = _advisory_lock_key("series", dicom_series_uid or orthanc_series_id)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

        # Ensure the parent study exists in DB before inserting the series
        result = await db.execute(select(Study).where(Study.study_uid == dicom_study_uid))
        if not result.scalar_one_or_none():
            await sync_study_to_db(db, parent_study_id, session=session)
            return  # Series will be re-synced as part of the study sync above

        if modality == "SEG":
            tags = await _fetch_first_instance_tags(session, series_info)
            volume_uid = _extract_referenced_series_uid_from_tags(tags)
            target_label = _extract_segment_label_from_tags(tags)
            await _get_or_create_segmentation(
                db=db,
                label_uid=dicom_series_uid,
                orthanc_series_id=orthanc_series_id,
                volume_uid=volume_uid,
                segmentation_type=desc or "Unknown",
                target_label=target_label,
                method="manual" if "MANUAL" in (desc or "").upper() else "auto",
                created_at=series_datetime,
            )
        else:
            await _get_or_create_volume(
                db=db,
                volume_uid=dicom_series_uid,
                orthanc_series_id=orthanc_series_id,
                study_uid=dicom_study_uid,
                desc=desc,
                modality=modality,
                num_slices=num_instances,
                created_at=series_datetime,
                body_part_examined=body_part,
            )
    finally:
        if own_session:
            await session.close()


# ----------------------------------------------------------------------
# DELETION HELPERS
# ----------------------------------------------------------------------

async def fetch_dicom_uid_for_deletion(
    session: aiohttp.ClientSession, level: str, orthanc_id: str
) -> str | None:
    """Attempt to fetch a resource's DICOM UID from Orthanc.

    NOTE: Orthanc's OnDeleted* Lua hooks fire *after* the resource is
    already removed, so a GET against the same resource id from those
    hooks will always 404 — this helper cannot resolve the TOCTOU that way.
    The actual (correct) resolution used by delete handling below is to
    look the DICOM UID up in our own database by its stored orthanc_*_id,
    since that row still exists at delete time. This function remains
    useful only for callers that have an orthanc_id for a resource that is
    *not* yet deleted (e.g. a pre-delete confirmation step), which nothing
    in this codebase currently does.
    """
    collection = {
        "patient": "patients",
        "study": "studies",
        "series": "series",
    }.get(level)

    if not collection:
        logger.warning("Unsupported Orthanc level for deletion lookup: %s", level)
        return None

    try:
        async with session.get(f"{ORTHANC_URL}/{collection}/{orthanc_id}") as resp:
            if resp.status != 200:
                logger.warning("Could not fetch %s %s for deletion.", level, orthanc_id)
                return None
            tags = (await resp.json()).get("MainDicomTags", {})
            return {
                "series": tags.get("SeriesInstanceUID"),
                "study": tags.get("StudyInstanceUID"),
                "patient": tags.get("PatientID"),
            }.get(level)
    except Exception as e:
        logger.error("Error fetching %s %s: %s", level, orthanc_id, e)
    return None


async def delete_series_from_db(db: AsyncSession, dicom_series_uid: str) -> bool:
    """Delete a Volume record by its DICOM Series UID."""
    result = await db.execute(
        select(Volume).where(Volume.volume_series_instance_uid == dicom_series_uid)
    )
    volume = result.scalar_one_or_none()
    if volume:
        await db.delete(volume)
        return True
    return False


async def delete_segmentation_from_db(db: AsyncSession, dicom_series_uid: str) -> bool:
    """Delete a Segmentation record by its DICOM Series UID."""
    result = await db.execute(
        select(Segmentation).where(Segmentation.label_series_instance_uid == dicom_series_uid)
    )
    seg = result.scalar_one_or_none()
    if seg:
        await db.delete(seg)
        return True
    return False


async def delete_study_from_db(db: AsyncSession, dicom_study_uid: str) -> bool:
    """Delete a Study record by its DICOM Study UID."""
    result = await db.execute(
        select(Study).where(Study.study_uid == dicom_study_uid)
    )
    study = result.scalar_one_or_none()
    if study:
        await db.delete(study)
        return True
    return False


async def delete_patient_from_db(db: AsyncSession, orthanc_patient_id: str) -> bool:
    """Delete a Patient record by its Orthanc Patient ID."""
    result = await db.execute(
        select(Patient).where(Patient.orthanc_patient_id == orthanc_patient_id)
    )
    patient = result.scalar_one_or_none()
    if patient:
        await db.delete(patient)
        return True
    return False
