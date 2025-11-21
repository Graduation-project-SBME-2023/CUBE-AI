# ===============================
# incremental_orthanc_sync.py
# ===============================

import aiohttp
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from models.patient import Patient
from models.study import Study
from models.volume import Volume
from models.segmentation import Segmentation

ORTHANC_URL = "http://orthanc-backend:8042"
AUTH = aiohttp.BasicAuth("orthanc", "orthanc")

# ----------------------------------------------------------------------
# HELPER FUNCTIONS
# ----------------------------------------------------------------------

def _parse_dicom_datetime(date_str, time_str):
    """Combine DICOM date/time strings like '20231130' + '141744.0' → datetime"""
    try:
        if not date_str:
            return datetime.utcnow()
        if not time_str:
            return datetime.strptime(date_str, "%Y%m%d")
        clean_time = time_str.split(".")[0].ljust(6, "0")  # remove decimals, pad
        return datetime.strptime(f"{date_str}{clean_time}", "%Y%m%d%H%M%S")
    except Exception:
        return datetime.utcnow()

async def _get_or_create_patient(db: AsyncSession, name: str, birth_date: str, gender: str):
    """Find or create patient by name + birth_date"""
    name = name or "Unknown"
    gender = gender if gender in ("M", "F") else None

    try:
        bdate = datetime.strptime(birth_date, "%Y%m%d").date() if birth_date else None
    except Exception:
        bdate = None

    query = select(Patient).where(Patient.name == name)
    if bdate:
        query = query.where(Patient.birth_date == bdate)
    result = await db.execute(query)
    patient = result.scalar_one_or_none()

    if patient:
        # optional: update gender
        if gender and patient.gender != gender:
            patient.gender = gender
            db.add(patient)
        return patient

    # Create new patient
    patient = Patient(
        name=name,
        birth_date=bdate,
        gender=gender,
        created_at=datetime.utcnow(),
    )
    db.add(patient)
    try:
        await db.flush()
    except Exception as e:
        # Handle race condition: another task already created this patient
        if "duplicate key" in str(e).lower() or "unique constraint" in str(e).lower():
            print(f"[INFO] Patient {name} was already created by another task, fetching it...")
            await db.rollback()
            query = select(Patient).where(Patient.name == name)
            if bdate:
                query = query.where(Patient.birth_date == bdate)
            result = await db.execute(query)
            patient = result.scalar_one_or_none()
            if not patient:
                raise  # If still not found, re-raise the error
        else:
            raise
    return patient

async def _get_or_create_study(db: AsyncSession, study_uid: str, patient_id: int, description: str, modality: str, created_at: datetime):
    result = await db.execute(select(Study).where(Study.study_uid == study_uid))
    study = result.scalar_one_or_none()
    if study:
        # Optional: update description/modality
        updated = False
        if description and study.description != description:
            study.description = description
            updated = True
        if modality and study.modality != modality:
            study.modality = modality
            updated = True
        if updated:
            db.add(study)
        return study

    study = Study(
        study_uid=study_uid,
        patient_id=patient_id,
        description=description or "Unknown",
        modality=(modality or "UNK")[:20],
        created_at=created_at,
    )
    db.add(study)
    try:
        await db.flush()
    except Exception as e:
        # Handle race condition: another task already created this study
        if "duplicate key" in str(e).lower() or "unique constraint" in str(e).lower():
            print(f"[INFO] Study {study_uid} was already created by another task, fetching it...")
            await db.rollback()
            result = await db.execute(select(Study).where(Study.study_uid == study_uid))
            study = result.scalar_one_or_none()
            if not study:
                raise  # If still not found, re-raise the error
        else:
            raise
    return study

async def _get_or_create_volume(db: AsyncSession, volume_uid: str, study_uid: str, desc: str, modality: str, num_slices: int, created_at: datetime):
    result = await db.execute(select(Volume).where(Volume.volume_series_instance_uid == volume_uid))
    volume = result.scalar_one_or_none()
    if volume:
        # Optional: update fields
        updated = False
        if desc and volume.series_description != desc:
            volume.series_description = desc
            updated = True
        if modality and volume.modality != modality:
            volume.modality = modality
            updated = True
        if num_slices and volume.num_slices != num_slices:
            volume.num_slices = num_slices
            updated = True
        if updated:
            db.add(volume)
        return volume

    volume = Volume(
        volume_series_instance_uid=volume_uid,
        study_uid=study_uid,
        series_description=desc or "Unknown",
        modality=modality or "UNK",
        num_slices=max(1, num_slices),
        created_at=created_at,
    )
    db.add(volume)
    try:
        await db.flush()
    except Exception as e:
        # Handle race condition: another task already created this volume
        if "duplicate key" in str(e).lower() or "unique constraint" in str(e).lower():
            print(f"[INFO] Volume {volume_uid} was already created by another task, fetching it...")
            await db.rollback()
            result = await db.execute(select(Volume).where(Volume.volume_series_instance_uid == volume_uid))
            volume = result.scalar_one_or_none()
            if not volume:
                raise  # If still not found, re-raise the error
        else:
            raise
    return volume

async def _get_or_create_segmentation(
    db: AsyncSession,
    label_uid: str,
    volume_uid: str,
    segmentation_type: str,
    method: str,
    created_at: datetime = None
):
    created_at = created_at or datetime.utcnow()  # <-- same logic as Study/Volume

    result = await db.execute(select(Segmentation).where(Segmentation.label_series_instance_uid == label_uid))
    seg = result.scalar_one_or_none()
    if seg:
        updated = False
        if segmentation_type and seg.segmentation_type != segmentation_type:
            seg.segmentation_type = segmentation_type
            updated = True
        if method and seg.method != method:
            seg.method = method
            updated = True
        if updated:
            db.add(seg)
        return seg

    if not volume_uid:
        print(f"[WARN] Skipping SEG {label_uid}: No referenced volume UID found.")
        return None

    v_check = await db.execute(select(Volume).where(Volume.volume_series_instance_uid == volume_uid))
    v = v_check.scalar_one_or_none()
    if not v:
        print(f"[WARN] Skipping SEG {label_uid}: Referenced volume {volume_uid} not found in DB.")
        return None

    seg = Segmentation(
        label_series_instance_uid=label_uid,
        volume_series_instance_uid=volume_uid,
        segmentation_type=segmentation_type,
        method=method,
        model_name=None,
        created_at=created_at,  # <-- use passed value
    )
    db.add(seg)
    try:
        await db.flush()
        print(f"[INFO] Added segmentation {label_uid} linked to volume {volume_uid}")
    except Exception as e:
        if "duplicate key" in str(e).lower() or "unique constraint" in str(e).lower():
            print(f"[INFO] Segmentation {label_uid} was already created by another task, fetching it...")
            await db.rollback()
            result = await db.execute(select(Segmentation).where(Segmentation.label_series_instance_uid == label_uid))
            seg = result.scalar_one_or_none()
            if not seg:
                raise
        else:
            raise
    return seg


async def _extract_referenced_series_uid(session: aiohttp.ClientSession, series_info: dict) -> str | None:
    """Extract referenced SeriesInstanceUID for SEG"""
    try:
        instances = series_info.get("Instances", [])
        if not instances:
            return None
        first_instance = instances[0]
        async with session.get(f"{ORTHANC_URL}/instances/{first_instance}/tags") as resp:
            if resp.status != 200:
                return None
            tags = await resp.json()
            ref_series_tag = tags.get("0008,1115")
            if ref_series_tag and ref_series_tag.get("Type") == "Sequence":
                seq = ref_series_tag.get("Value", [])
                if seq and "0020,000e" in seq[0]:
                    return seq[0]["0020,000e"]["Value"]
            # fallback logic omitted for brevity
    except Exception as e:
        print(f"[ERROR] Failed extracting referenced UID: {e}")
    return None

# ----------------------------------------------------------------------
# INITIAL FULL SYNC
# ----------------------------------------------------------------------

async def sync_all_from_orthanc(db: AsyncSession):
    """
    Sync all existing studies from Orthanc to the database.
    This should be called once at startup to populate the database.
    """
    print("[INFO] 🔄 Starting initial full sync from Orthanc...")
    
    try:
        async with aiohttp.ClientSession(auth=AUTH) as session:
            # Get all studies from Orthanc
            async with session.get(f"{ORTHANC_URL}/studies") as resp:
                if resp.status != 200:
                    print(f"[ERROR] Failed to fetch studies from Orthanc: {resp.status}")
                    return
                study_ids = await resp.json()
            
            total_studies = len(study_ids)
            print(f"[INFO] Found {total_studies} studies in Orthanc")
            
            if total_studies == 0:
                print("[INFO] ✅ No studies to sync")
                return
            
            # Sync each study
            synced = 0
            failed = 0
            for idx, study_id in enumerate(study_ids, 1):
                try:
                    print(f"[INFO] Syncing study {idx}/{total_studies}: {study_id}")
                    await sync_study_to_db(db, study_id, session=session)
                    await db.commit()
                    synced += 1
                except Exception as e:
                    print(f"[ERROR] Failed to sync study {study_id}: {e}")
                    await db.rollback()
                    failed += 1
            
            print(f"[INFO] ✅ Initial sync completed: {synced} synced, {failed} failed")
                
    except Exception as e:
        print(f"[ERROR] Initial sync failed: {e}")
        import traceback
        traceback.print_exc()

# ----------------------------------------------------------------------
# SYNC FUNCTIONS
# ----------------------------------------------------------------------

async def sync_study_to_db(db: AsyncSession, study_uid: str, session: aiohttp.ClientSession = None):
    """Sync a single study (and all its series)"""
    own_session = False
    if not session:
        session = aiohttp.ClientSession(auth=AUTH)
        own_session = True
    
    try:
        async with session.get(f"{ORTHANC_URL}/studies/{study_uid}") as resp:
            if resp.status != 200:
                print(f"[WARN] Study {study_uid} not found in Orthanc.")
                return
            study_info = await resp.json()

        tags = study_info.get("MainDicomTags", {})
        patient_id_orthanc = study_info.get("ParentPatient")
        async with session.get(f"{ORTHANC_URL}/patients/{patient_id_orthanc}") as resp:
            patient_info = await resp.json()
        p_tags = patient_info.get("MainDicomTags", {})
        patient = await _get_or_create_patient(
            db=db,
            name=p_tags.get("PatientName", "Unknown"),
            birth_date=p_tags.get("PatientBirthDate"),
            gender=p_tags.get("PatientSex"),
        )

        study_datetime = _parse_dicom_datetime(tags.get("StudyDate"), tags.get("StudyTime"))
        db_study = await _get_or_create_study(
            db=db,
            study_uid=tags.get("StudyInstanceUID"),
            patient_id=patient.patient_id,
            description=tags.get("StudyDescription"),
            modality=tags.get("ModalitiesInStudy"),
            created_at=study_datetime,
        )

        # sync series
        for series_id in study_info.get("Series", []):
            await sync_series_to_db(db, series_id, session=session)
    finally:
        if own_session:
            await session.close()

async def sync_series_to_db(db: AsyncSession, series_id: str, session: aiohttp.ClientSession = None):
    """Sync a single series (volume or SEG)"""
    own_session = False
    if not session:
        session = aiohttp.ClientSession(auth=AUTH)
        own_session = True

    try:
        async with session.get(f"{ORTHANC_URL}/series/{series_id}") as resp:
            if resp.status != 200:
                print(f"[WARN] Series {series_id} not found in Orthanc.")
                return
            series_info = await resp.json()

        s_tags = series_info.get("MainDicomTags", {})
        dicom_series_uid = s_tags.get("SeriesInstanceUID")
        modality = (s_tags.get("Modality") or "").upper()
        desc = s_tags.get("SeriesDescription")
        num_instances = len(series_info.get("Instances", []))
        series_datetime = _parse_dicom_datetime(s_tags.get("SeriesDate"), s_tags.get("SeriesTime"))
        
        # Get the parent study's DICOM UID (not the Orthanc resource ID!)
        parent_study_id = series_info.get("ParentStudy")
        async with session.get(f"{ORTHANC_URL}/studies/{parent_study_id}") as resp:
            if resp.status != 200:
                print(f"[WARN] Parent study {parent_study_id} not found in Orthanc.")
                return
            study_info = await resp.json()
        study_tags = study_info.get("MainDicomTags", {})
        dicom_study_uid = study_tags.get("StudyInstanceUID")
        
        # CRITICAL FIX: Ensure the parent study exists in DB first!
        # Check if study exists in database
        result = await db.execute(select(Study).where(Study.study_uid == dicom_study_uid))
        existing_study = result.scalar_one_or_none()
        
        if not existing_study:
            print(f"[INFO] Parent study {dicom_study_uid} not in DB yet, syncing it first...")
            # Sync the entire study (which will also sync this series)
            await sync_study_to_db(db, parent_study_id, session=session)
            return  # The study sync already processed this series

        if modality == "SEG":
            volume_uid = await _extract_referenced_series_uid(session, series_info)
            series_datetime = _parse_dicom_datetime(s_tags.get("SeriesDate"), s_tags.get("SeriesTime"))
            await _get_or_create_segmentation(
                db=db,
                label_uid=dicom_series_uid,
                volume_uid=volume_uid,
                segmentation_type=desc or "Unknown",
                method="manual" if "MANUAL" in (desc or "").upper() else "auto",
                created_at=series_datetime  
            )

        else:
            await _get_or_create_volume(
                db=db,
                volume_uid=dicom_series_uid,
                study_uid=dicom_study_uid,  # Use DICOM UID, not Orthanc resource ID
                desc=desc,
                modality=modality,
                num_slices=num_instances,
                created_at=series_datetime,
            )

    finally:
        if own_session:
            await session.close()

