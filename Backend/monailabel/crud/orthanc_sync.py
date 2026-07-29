# ===============================
# orthanc_sync.py - Incremental Orthanc to database sync
# ===============================
import logging
import aiohttp
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from models.patient import Patient
from models.study import Study
from models.volume import Volume
from models.segmentation import Segmentation
from services.orthanc_client import ORTHANC_URL, create_orthanc_session

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# HELPER FUNCTIONS
# ----------------------------------------------------------------------

def _parse_dicom_datetime(date_str: str, time_str: str) -> datetime:
    """Combine DICOM date/time strings like '20231130' + '141744.0'."""
    try:
        if not date_str:
            return datetime.utcnow()
        if not time_str:
            return datetime.strptime(date_str, "%Y%m%d")
        clean_time = time_str.split(".")[0].ljust(6, "0")  # remove decimals, pad to HHMMSS
        return datetime.strptime(f"{date_str}{clean_time}", "%Y%m%d%H%M%S")
    except Exception:
        return datetime.utcnow()


def _is_duplicate_error(exc: Exception) -> bool:
    """Return True if the exception is a database unique constraint violation."""
    msg = str(exc).lower()
    return "duplicate key" in msg or "unique constraint" in msg


async def _refetch_after_duplicate(db: AsyncSession, model_class: Any, where_clause: Any):
    """
    After a duplicate key error, roll back and re-fetch the existing record.
    Raises if the re-fetch still returns nothing (unexpected state).
    """
    await db.rollback()
    result = await db.execute(select(model_class).where(where_clause))
    record = result.scalar_one_or_none()
    if record:
        return record
    raise RuntimeError(f"Could not re-fetch {model_class.__name__} after duplicate error.")


# ----------------------------------------------------------------------
# GET-OR-CREATE HELPERS
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
    gender = gender if gender in ("M", "F") else None

    try:
        bdate = datetime.strptime(birth_date, "%Y%m%d").date() if birth_date else None
    except Exception:
        bdate = None

    result = await db.execute(
        select(Patient).where(Patient.orthanc_patient_id == orthanc_patient_id)
    )
    patient = result.scalar_one_or_none()

    if patient:
        # Only fill missing gender; do not overwrite manual edits
        if gender and not patient.gender:
            patient.gender = gender
            db.add(patient)
        return patient

    patient = Patient(
        name=name,
        orthanc_patient_id=orthanc_patient_id,
        birth_date=bdate,
        gender=gender,
        created_at=datetime.utcnow(),
    )
    db.add(patient)
    try:
        await db.flush()
    except Exception as e:
        if _is_duplicate_error(e):
            return await _refetch_after_duplicate(
                db, Patient, Patient.orthanc_patient_id == orthanc_patient_id
            )
        raise
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
    """Find or create a Study record. Preserves manual edits on existing records."""
    result = await db.execute(select(Study).where(Study.study_uid == study_uid))
    study = result.scalar_one_or_none()

    if study:
        updated = False
        if not study.orthanc_study_id:
            study.orthanc_study_id = orthanc_study_id
            updated = True
        if description and not study.description:
            study.description = description
            updated = True
        if modality and not study.modality:
            study.modality = modality
            updated = True
        if updated:
            db.add(study)
        return study

    study = Study(
        study_uid=study_uid,
        orthanc_study_id=orthanc_study_id,
        patient_id=patient_id,
        description=description or "Unknown",
        modality=(modality or "UNK")[:20],
        created_at=created_at,
    )
    db.add(study)
    try:
        await db.flush()
    except Exception as e:
        if _is_duplicate_error(e):
            return await _refetch_after_duplicate(db, Study, Study.study_uid == study_uid)
        raise
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
    result = await db.execute(
        select(Volume).where(Volume.volume_series_instance_uid == volume_uid)
    )
    volume = result.scalar_one_or_none()

    if volume:
        updated = False
        if not volume.orthanc_series_id:
            volume.orthanc_series_id = orthanc_series_id
            updated = True
        if desc and not volume.series_description:
            volume.series_description = desc
            updated = True
        if modality and not volume.modality:
            volume.modality = modality
            updated = True
        if body_part_examined and not volume.body_part_examined:
            volume.body_part_examined = body_part_examined
            updated = True
        if num_slices and not volume.num_slices:
            volume.num_slices = num_slices
            updated = True
        if updated:
            db.add(volume)
        return volume

    volume = Volume(
        volume_series_instance_uid=volume_uid,
        orthanc_series_id=orthanc_series_id,
        study_uid=study_uid,
        series_description=desc or "Unknown",
        modality=modality or "UNK",
        num_slices=max(1, num_slices),
        created_at=created_at,
        body_part_examined=body_part_examined,
    )
    db.add(volume)
    try:
        await db.flush()
    except Exception as e:
        if _is_duplicate_error(e):
            return await _refetch_after_duplicate(
                db, Volume, Volume.volume_series_instance_uid == volume_uid
            )
        raise
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
    """Find or create a Segmentation record. Returns None if the referenced volume is missing."""
    created_at = created_at or datetime.utcnow()

    result = await db.execute(
        select(Segmentation).where(Segmentation.label_series_instance_uid == label_uid)
    )
    seg = result.scalar_one_or_none()

    if seg:
        updated = False
        if not seg.orthanc_series_id:
            seg.orthanc_series_id = orthanc_series_id
            updated = True
        if segmentation_type and not seg.segmentation_type:
            seg.segmentation_type = segmentation_type
            updated = True
        if target_label and not seg.target_label:
            seg.target_label = target_label
            updated = True
        if method and not seg.method:
            seg.method = method
            updated = True
        if updated:
            db.add(seg)
        return seg

    if not volume_uid:
        logger.warning("Skipping SEG %s: No referenced volume UID found.", label_uid)
        return None

    v_result = await db.execute(
        select(Volume).where(Volume.volume_series_instance_uid == volume_uid)
    )
    if not v_result.scalar_one_or_none():
        logger.warning("Skipping SEG %s: Referenced volume %s not in DB.", label_uid, volume_uid)
        return None

    seg = Segmentation(
        label_series_instance_uid=label_uid,
        orthanc_series_id=orthanc_series_id,
        volume_series_instance_uid=volume_uid,
        segmentation_type=segmentation_type,
        target_label=target_label,
        method=method,
        name=None,
        created_at=created_at,
    )
    db.add(seg)
    try:
        await db.flush()
    except Exception as e:
        if _is_duplicate_error(e):
            return await _refetch_after_duplicate(
                db, Segmentation, Segmentation.label_series_instance_uid == label_uid
            )
        raise
    return seg


# ----------------------------------------------------------------------
# EXTRACT REFERENCED SERIES UID FOR SEG
# ----------------------------------------------------------------------

async def _extract_referenced_series_uid(
    session: aiohttp.ClientSession, series_info: dict
) -> str | None:
    """Extract the referenced SeriesInstanceUID from a SEG series' DICOM tags."""
    try:
        instances = series_info.get("Instances", [])
        if not instances:
            return None
        async with session.get(f"{ORTHANC_URL}/instances/{instances[0]}/tags") as resp:
            if resp.status != 200:
                return None
            tags = await resp.json()
            ref_series_tag = tags.get("0008,1115")
            if ref_series_tag and ref_series_tag.get("Type") == "Sequence":
                seq = ref_series_tag.get("Value", [])
                if seq and "0020,000e" in seq[0]:
                    return seq[0]["0020,000e"]["Value"]
    except Exception as e:
        logger.error("Failed extracting referenced UID: %s", e)
    return None


async def _extract_segment_label(
    session: aiohttp.ClientSession, series_info: dict
) -> str | None:
    """Return the first DICOM SegmentLabel (0062,0005) for a SEG series.

    The application writes one target per DICOM-SEG series.  A missing value is
    intentionally returned as ``None`` so legacy records remain distinguishable
    from records with structured label metadata.
    """
    try:
        instances = series_info.get("Instances", [])
        if not instances:
            return None
        async with session.get(f"{ORTHANC_URL}/instances/{instances[0]}/tags") as resp:
            if resp.status != 200:
                return None
            tags = await resp.json()

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
    except Exception as exc:
        logger.warning("Failed extracting SegmentLabel: %s", exc)
        return None


# ----------------------------------------------------------------------
# FULL SYNC
# ----------------------------------------------------------------------

async def sync_all_from_orthanc(db: AsyncSession) -> None:
    """Sync all existing studies from Orthanc (full sync at startup)."""
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

        patient_orthanc_id = study_info.get("ParentPatient")
        async with session.get(f"{ORTHANC_URL}/patients/{patient_orthanc_id}") as resp:
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
            created_at=_parse_dicom_datetime(tags.get("StudyDate"), tags.get("StudyTime")),
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
        series_datetime = _parse_dicom_datetime(s_tags.get("SeriesDate"), s_tags.get("SeriesTime"))

        parent_study_id = series_info.get("ParentStudy")
        async with session.get(f"{ORTHANC_URL}/studies/{parent_study_id}") as resp:
            if resp.status != 200:
                logger.warning("Parent study %s not found in Orthanc.", parent_study_id)
                return
            study_info = await resp.json()

        dicom_study_uid = study_info.get("MainDicomTags", {}).get("StudyInstanceUID")

        # Ensure the parent study exists in DB before inserting the series
        result = await db.execute(select(Study).where(Study.study_uid == dicom_study_uid))
        if not result.scalar_one_or_none():
            await sync_study_to_db(db, parent_study_id, session=session)
            return  # Series will be re-synced as part of the study sync above

        if modality == "SEG":
            volume_uid = await _extract_referenced_series_uid(session, series_info)
            target_label = await _extract_segment_label(session, series_info)
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
    """Fetch the DICOM UID from Orthanc before a resource is deleted."""
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
