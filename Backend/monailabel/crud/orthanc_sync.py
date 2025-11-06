import aiohttp
from datetime import datetime, date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from models.patient import Patient
from models.study import Study
from models.volume import Volume
from models.segmentation import Segmentation

ORTHANC_URL = "http://orthanc-backend:8042"
AUTH = aiohttp.BasicAuth("orthanc", "orthanc")  


async def sync_orthanc_to_db(db: AsyncSession):
    """
    Asynchronously fetch metadata from Orthanc (Patients → Studies → Series)
    and sync with PostgreSQL according to schema.
    """
    async with aiohttp.ClientSession(auth=AUTH) as session:
        # --- Get all study IDs (Orthanc internal IDs) ---
        async with session.get(f"{ORTHANC_URL}/studies") as resp:
            orthanc_study_ids = await resp.json()

        for orthanc_study_id in orthanc_study_ids:
            # --- Fetch study metadata ---
            async with session.get(f"{ORTHANC_URL}/studies/{orthanc_study_id}") as resp:
                study_info = await resp.json()

            tags = study_info.get("MainDicomTags", {})

            # ✅ True DICOM identifiers
            dicom_study_uid = tags.get("StudyInstanceUID")
            study_date = tags.get("StudyDate")
            study_time = tags.get("StudyTime")

            # --- Fetch patient info from Orthanc ---
            patient_id_orthanc = study_info.get("ParentPatient")
            async with session.get(f"{ORTHANC_URL}/patients/{patient_id_orthanc}") as resp:
                patient_info = await resp.json()

            p_tags = patient_info.get("MainDicomTags", {})
            patient_name = p_tags.get("PatientName", "Unknown")
            birth_date = p_tags.get("PatientBirthDate")
            gender = p_tags.get("PatientSex")

            # --- PATIENT ---
            patient = await _get_or_create_patient(
                db=db,
                name=patient_name,
                birth_date=birth_date,
                gender=gender,
            )

            # --- Parse Study Date/Time ---
            study_datetime = _parse_dicom_datetime(study_date, study_time)

            # --- STUDY ---
            db_study = await _get_or_create_study(
                db=db,
                study_uid=dicom_study_uid,
                patient_id=patient.patient_id,
                description=tags.get("StudyDescription"),
                modality=tags.get("ModalitiesInStudy"),
                created_at=study_datetime,
            )

            # --- SERIES LOOP (Volumes or SEGs) ---
            for orthanc_series_id in study_info.get("Series", []):
                async with session.get(f"{ORTHANC_URL}/series/{orthanc_series_id}") as resp:
                    series_info = await resp.json()

                s_tags = series_info.get("MainDicomTags", {})
                dicom_series_uid = s_tags.get("SeriesInstanceUID")
                modality = s_tags.get("Modality", "").upper()
                desc = s_tags.get("SeriesDescription")
                num_instances = len(series_info.get("Instances", []))
                series_date = s_tags.get("SeriesDate")
                series_time = s_tags.get("SeriesTime")

                series_datetime = _parse_dicom_datetime(series_date, series_time)

                if modality == "SEG":
                    await _get_or_create_segmentation(
                        db=db,
                        label_uid=dicom_series_uid,
                        volume_uid=await _extract_referenced_series_uid(session, series_info),

                        segmentation_type=desc or "Unknown",
                        method="manual" if "MANUAL" in (desc or "").upper() else "auto",
                    )

                else:
                    await _get_or_create_volume(
                        db=db,
                        volume_uid=dicom_series_uid,
                        study_uid=dicom_study_uid,
                        desc=desc,
                        modality=modality,
                        num_slices=num_instances,
                        created_at=series_datetime,
                    )

        await db.commit()
        print("✅ Orthanc metadata synced: Patients, Studies, Volumes, Segmentations.")


# ----------------------------------------------------------------------
# Helper functions
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
    """Ensure patient record exists; create if missing, using name + birth_date match."""
    name = name or "Unknown"
    gender = gender if gender in ("M", "F") else None

    try:
        bdate = datetime.strptime(birth_date, "%Y%m%d").date() if birth_date else None
    except Exception:
        bdate = None

    # Try to find existing patient by name and birth date (optional matching rule)
    query = select(Patient).where(Patient.name == name)
    if bdate:
        query = query.where(Patient.birth_date == bdate)
    result = await db.execute(query)
    patient = result.scalar_one_or_none()

    # If not found, create new patient (ID is auto-generated)
    if not patient:
        patient = Patient(
            name=name,
            birth_date=bdate,
            gender=gender,
            created_at=datetime.utcnow(),
        )
        db.add(patient)
        await db.flush()  # ensures patient.patient_id is assigned

    return patient



async def _get_or_create_study(db: AsyncSession, study_uid: str, patient_id: int, description: str, modality: str, created_at: datetime):
    """Ensure study record exists; create if missing."""
    result = await db.execute(select(Study).where(Study.study_uid == study_uid))
    study = result.scalar_one_or_none()

    if not study:
        study = Study(
            study_uid=study_uid,
            patient_id=patient_id,
            description=description or "Unknown",
            modality=(modality or "UNK")[:20],
            created_at=created_at,
        )
        db.add(study)
        await db.flush()

    return study


async def _get_or_create_volume(db: AsyncSession, volume_uid: str, study_uid: str, desc: str, modality: str, num_slices: int, created_at: datetime):
    """Ensure imaging series record exists; create if missing."""
    result = await db.execute(select(Volume).where(Volume.volume_series_instance_uid == volume_uid))
    volume = result.scalar_one_or_none()

    if not volume:
        volume = Volume(
            volume_series_instance_uid=volume_uid,
            study_uid=study_uid,
            series_description=desc or "Unknown",
            modality=modality or "UNK",
            num_slices=max(1, num_slices),
            created_at=created_at,
        )
        db.add(volume)
        await db.flush()

    return volume


async def _get_or_create_segmentation(db: AsyncSession, label_uid: str, volume_uid: str, segmentation_type: str, method: str):
    result = await db.execute(select(Segmentation).where(Segmentation.label_series_instance_uid == label_uid))
    seg = result.scalar_one_or_none()
    if seg:
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
        created_at=datetime.utcnow(),
    )
    db.add(seg)
    await db.flush()
    print(f"[INFO] Added segmentation {label_uid} linked to volume {volume_uid}")
    return seg



async def _extract_referenced_series_uid(session: aiohttp.ClientSession, series_info: dict) -> str | None:
    """
    Extract the referenced SeriesInstanceUID for a SEG object from Orthanc.
    Uses the /tags endpoint to access nested DICOM sequences properly.
    """
    try:
        instances = series_info.get("Instances", [])
        if not instances:
            print("[WARN] SEG has no instances.")
            return None

        first_instance = instances[0]

        # Fetch full DICOM tags from Orthanc (without simplify to get raw structure)
        async with session.get(f"{ORTHANC_URL}/instances/{first_instance}/tags") as resp:
            if resp.status != 200:
                print(f"[WARN] Failed to fetch SEG instance tags {first_instance}: {resp.status}")
                return None
            tags = await resp.json()

        # 1️⃣ Check ReferencedSeriesSequence (0008,1115)
        ref_series_tag = tags.get("0008,1115")
        if ref_series_tag and ref_series_tag.get("Type") == "Sequence":
            sequence_value = ref_series_tag.get("Value", [])
            if isinstance(sequence_value, list) and len(sequence_value) > 0:
                # Access SeriesInstanceUID (0020,000e) within the sequence
                first_item = sequence_value[0]
                series_uid_tag = first_item.get("0020,000e")
                if series_uid_tag:
                    series_uid = series_uid_tag.get("Value")
                    if series_uid:
                        print(f"[INFO] Found ReferencedSeriesSequence → {series_uid}")
                        return series_uid

        # 2️⃣ Fallback: Check ReferencedInstanceSequence (0008,114a)
        ref_inst_tag = tags.get("0008,114a")
        if ref_inst_tag and ref_inst_tag.get("Type") == "Sequence":
            sequence_value = ref_inst_tag.get("Value", [])
            if isinstance(sequence_value, list) and len(sequence_value) > 0:
                # Get first ReferencedSOPInstanceUID (0008,1155)
                first_item = sequence_value[0]
                sop_uid_tag = first_item.get("0008,1155")
                if sop_uid_tag:
                    sop_uid = sop_uid_tag.get("Value")
                    if sop_uid:
                        print(f"[INFO] Resolving SeriesInstanceUID from SOP {sop_uid}")
                        # Try to find this instance in Orthanc by SOP UID
                        async with session.post(
                            f"{ORTHANC_URL}/tools/lookup",
                            json=sop_uid
                        ) as lookup_resp:
                            if lookup_resp.status == 200:
                                lookup_result = await lookup_resp.json()
                                if lookup_result and len(lookup_result) > 0:
                                    instance_id = lookup_result[0].get("ID")
                                    if instance_id:
                                        # Get parent series
                                        async with session.get(f"{ORTHANC_URL}/instances/{instance_id}") as r2:
                                            if r2.status == 200:
                                                inst_data = await r2.json()
                                                parent_series = inst_data.get("ParentSeries")
                                                if parent_series:
                                                    # Get SeriesInstanceUID from series
                                                    async with session.get(f"{ORTHANC_URL}/series/{parent_series}") as r3:
                                                        if r3.status == 200:
                                                            series_data = await r3.json()
                                                            series_uid = series_data.get("MainDicomTags", {}).get("SeriesInstanceUID")
                                                            if series_uid:
                                                                print(f"[INFO] Resolved to SeriesInstanceUID → {series_uid}")
                                                                return series_uid

        print(f"[WARN] Could not find referenced series UID for SEG instance {first_instance}")

    except Exception as e:
        print(f"[ERROR] Failed extracting referenced UID for SEG: {e}")
        import traceback
        traceback.print_exc()

    return None