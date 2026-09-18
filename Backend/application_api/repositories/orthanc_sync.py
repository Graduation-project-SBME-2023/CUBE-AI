# ===============================
# orthanc_sync.py - Incremental Orthanc to database sync
# ===============================
import logging
import zlib
from datetime import datetime, timezone

import aiohttp
from sqlalchemy import text, update
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
    """Combine DICOM date/time strings like '20231130' + '141744.0'."""
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
    """Deterministic 32-bit key for pg_advisory_xact_lock from string parts."""
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
    updated = False
    if gender and not patient.gender:
        patient.gender = gender
        updated = True
    if patient.archive_status != "available":
        patient.archive_status = "available"
        updated = True
    if updated:
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
    """Find or create a Study record."""
    stmt = (
        pg_insert(Study)
        .values(
            study_uid=study_uid,
            orthanc_study_id=orthanc_study_id,
            patient_id=patient_id,
            description=description or "Unknown",
            modality=(modality or "UNK")[:20],
            study_datetime=created_at,
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
    if study.archive_status != "available":
        study.archive_status = "available"
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
    """Find or create a Volume (non-SEG series) record."""
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
            series_datetime=created_at,
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
    if volume.archive_status != "available":
        volume.archive_status = "available"
        updated = True
    if updated:
        db.add(volume)
    return volume


async def _get_or_create_segmentation(
    db: AsyncSession,
    label_uid: str,
    orthanc_series_id: str,
    volume_uid: str,
    series_description: str,
    target_label: str | None,
    generation_method: str,
    created_at: datetime = None,
) -> Segmentation | None:
    """Find or create a Segmentation record. Returns None if the referenced volume is missing."""
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
            series_description=series_description,
            target_label=target_label,
            generation_method=generation_method,
            # The sync knows only that a SEG appeared in Orthanc. It cannot
            # tell an assisted mask from a pipeline mask — both emit the same
            # `dd/mm/yyyy - model - label` SeriesDescription — so it records
            # `imported`, which is not reusable inventory. A producer declares
            # the real origin through POST /segmentations/declare-origin.
            origin="imported",
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
    # A row created by a producer's origin declaration seeds orthanc_series_id
    # with the DICOM UID because the Orthanc id is not known yet; replace it
    # with the real one as soon as the sync resolves it.
    if not seg.orthanc_series_id or seg.orthanc_series_id == label_uid:
        seg.orthanc_series_id = orthanc_series_id
        updated = True
    if series_description and seg.series_description != series_description:
        seg.series_description = series_description
        updated = True
    if target_label and seg.target_label != target_label:
        seg.target_label = target_label
        updated = True
    if generation_method and seg.generation_method != generation_method:
        seg.generation_method = generation_method
        updated = True
    # A SEG that is back in Orthanc is available again.
    if seg.archive_status != "available":
        seg.archive_status = "available"
        updated = True
    # `origin` and `is_empty_mask` are NEVER written here: only a producer
    # knows them, and overwriting a declaration with `imported` would put an
    # unverified mask back into reuse.
    if updated:
        db.add(seg)
    return seg


# ----------------------------------------------------------------------
# EXTRACT REFERENCED SERIES UID FOR SEG
# ----------------------------------------------------------------------

async def _fetch_first_instance_tags(
    session: aiohttp.ClientSession, series_info: dict
) -> dict | None:
    """Fetch the DICOM tags of a series' first instance, once."""
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
    """Return the first DICOM SegmentLabel (0062,0005) for a SEG series."""
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
    """Sync all existing studies from Orthanc."""
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
    """Sync a single study and all its series from Orthanc to the database."""
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
                series_description=desc or "Unknown",
                target_label=target_label,
                # This records *how the description reads*, not provenance —
                # `origin` is the provenance column, and only a producer sets
                # it. Keeping the old sniff here would be a lie about method
                # too, so it is narrowed to the one case the string really
                # does carry: an operator-edited "Modification - ..." mask.
                generation_method=(
                    "modification"
                    if (desc or "").strip().upper().startswith("MODIFICATION")
                    else "unknown"
                ),
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
    """Attempt to fetch a resource's DICOM UID from Orthanc."""
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


async def mark_series_unavailable(db: AsyncSession, dicom_series_uid: str) -> bool:
    """Mark a Volume unavailable after Orthanc lost it."""
    result = await db.execute(
        select(Volume).where(Volume.volume_series_instance_uid == dicom_series_uid)
    )
    volume = result.scalar_one_or_none()
    if volume:
        volume.archive_status = "unavailable"
        volume.updated_at = datetime.now(timezone.utc)
        db.add(volume)
        return True
    return False


async def mark_segmentation_unavailable(db: AsyncSession, dicom_series_uid: str) -> bool:
    """Mark a Segmentation unavailable after Orthanc lost it."""
    result = await db.execute(
        select(Segmentation).where(Segmentation.label_series_instance_uid == dicom_series_uid)
    )
    seg = result.scalar_one_or_none()
    if seg:
        seg.archive_status = "unavailable"
        db.add(seg)
        return True
    return False


async def mark_study_unavailable(db: AsyncSession, dicom_study_uid: str) -> bool:
    """Mark a Study and everything under it unavailable."""
    result = await db.execute(
        select(Study).where(Study.study_uid == dicom_study_uid)
    )
    study = result.scalar_one_or_none()
    if not study:
        return False
    study.archive_status = "unavailable"
    study.updated_at = datetime.now(timezone.utc)
    db.add(study)
    await db.execute(
        update(Volume)
        .where(Volume.study_uid == dicom_study_uid)
        .values(archive_status="unavailable", updated_at=datetime.now(timezone.utc))
    )
    await db.execute(
        update(Segmentation)
        .where(
            Segmentation.volume_series_instance_uid.in_(
                select(Volume.volume_series_instance_uid).where(
                    Volume.study_uid == dicom_study_uid
                )
            )
        )
        .values(archive_status="unavailable")
    )
    return True


async def mark_patient_unavailable(db: AsyncSession, orthanc_patient_id: str) -> bool:
    """Mark a Patient and its whole subtree unavailable."""
    result = await db.execute(
        select(Patient).where(Patient.orthanc_patient_id == orthanc_patient_id)
    )
    patient = result.scalar_one_or_none()
    if not patient:
        return False
    patient.archive_status = "unavailable"
    patient.updated_at = datetime.now(timezone.utc)
    db.add(patient)
    study_uids = (
        await db.execute(
            select(Study.study_uid).where(Study.patient_id == patient.patient_id)
        )
    ).scalars().all()
    for study_uid in study_uids:
        await mark_study_unavailable(db, study_uid)
    return True
