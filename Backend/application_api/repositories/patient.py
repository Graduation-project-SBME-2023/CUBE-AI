import logging
from datetime import date, datetime
from typing import Optional, Union
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError
from application_api.db.models.patient import Patient
from application_api.db.models.study import Study
from application_api.db.models.volume import Volume
from application_api.db.models.segmentation import Segmentation
from application_api.schemas.patient import PatientCreate, PatientUpdate
from application_api.services.orthanc_client import cleanup_orthanc_resources_after_db_delete

logger = logging.getLogger(__name__)


def normalize_gender(gender: Optional[str]) -> Optional[str]:
    if not gender:
        return None
    gender = gender.strip().upper()
    if gender in ["M", "MALE"]:
        return "M"
    elif gender in ["F", "FEMALE"]:
        return "F"
    return None  # unknown/other values

def normalize_birth_date(birth_date: Optional[Union[str, date]]) -> Optional[date]:
    if not birth_date:
        return None

    # If it's already a date, return as-is
    if isinstance(birth_date, date):
        return birth_date

    # Otherwise, try parsing string formats
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(birth_date, fmt).date()
        except ValueError:
            continue
    return None  # invalid format

async def get_patient(db: AsyncSession, patient_id: UUID):
    result = await db.execute(
        select(Patient).where(Patient.patient_id == patient_id)
    )
    return result.scalar_one_or_none()

async def get_patients(db: AsyncSession, skip: int = 0, limit: int = 100):
    result = await db.execute(select(Patient).offset(skip).limit(limit))
    return result.scalars().all()

async def create_patient(db: AsyncSession, patient: PatientCreate):
    patient_data = patient.dict()
    patient_data["gender"] = normalize_gender(patient_data.get("gender"))
    patient_data["birth_date"] = normalize_birth_date(patient_data.get("birth_date"))

    db_patient = Patient(**patient_data)
    db.add(db_patient)
    try:
        await db.commit()
        await db.refresh(db_patient)
        return db_patient
    except SQLAlchemyError as e:
        await db.rollback()
        raise e

async def update_patient(db: AsyncSession, patient_id: UUID, data: PatientUpdate):
    result = await db.execute(select(Patient).where(Patient.patient_id == patient_id))
    patient = result.scalar_one_or_none()
    if not patient:
        return None
    update_data = data.dict(exclude_unset=True)
    if "gender" in update_data:
        update_data["gender"] = normalize_gender(update_data["gender"])
    if "birth_date" in update_data:
        update_data["birth_date"] = normalize_birth_date(update_data["birth_date"])

    for field, value in update_data.items():
        setattr(patient, field, value)
    try:
        await db.commit()
        await db.refresh(patient)
        return patient
    except SQLAlchemyError as e:
        await db.rollback()
        raise e
    

async def delete_patient(db: AsyncSession, patient_id: UUID):
    """
    Delete patient from both the database and Orthanc.
    Returns (success: bool, error_message: str | None)

    Database-first (see repositories/volume.py:delete_volume for the full
    rationale). A patient delete cascades in the DB through studies ->
    volume_series -> segmentations; every Orthanc id in that subtree is
    collected before the delete so PACS cleanup is complete.
    """
    result = await db.execute(select(Patient).where(Patient.patient_id == patient_id))
    patient = result.scalar_one_or_none()

    if not patient:
        return False, "Patient not found"

    orthanc_patient_id = patient.orthanc_patient_id

    study_result = await db.execute(
        select(Study.study_uid, Study.orthanc_study_id).where(Study.patient_id == patient_id)
    )
    studies = study_result.all()
    study_orthanc_ids = [row.orthanc_study_id for row in studies]

    volume_orthanc_ids: list[str | None] = []
    seg_orthanc_ids: list[str | None] = []
    if studies:
        study_uids = [row.study_uid for row in studies]
        volume_result = await db.execute(
            select(Volume.volume_series_instance_uid, Volume.orthanc_series_id).where(
                Volume.study_uid.in_(study_uids)
            )
        )
        volumes = volume_result.all()
        volume_orthanc_ids = [row.orthanc_series_id for row in volumes]

        if volumes:
            volume_uids = [row.volume_series_instance_uid for row in volumes]
            seg_result = await db.execute(
                select(Segmentation.orthanc_series_id).where(
                    Segmentation.volume_series_instance_uid.in_(volume_uids)
                )
            )
            seg_orthanc_ids = [row[0] for row in seg_result.all()]

    try:
        await db.delete(patient)  # cascades to studies -> volume_series -> segmentations
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        error_msg = f"Database error: {str(e)}"
        logger.error(error_msg)
        return False, error_msg

    await cleanup_orthanc_resources_after_db_delete(
        "series",
        [*volume_orthanc_ids, *seg_orthanc_ids],
        context=f"DB delete of patient {patient_id}",
    )
    await cleanup_orthanc_resources_after_db_delete(
        "studies",
        study_orthanc_ids,
        context=f"DB delete of patient {patient_id}",
    )
    await cleanup_orthanc_resources_after_db_delete(
        "patients",
        [orthanc_patient_id],
        context=f"DB delete of patient {patient_id}",
    )
    return True, None

# ---------------------------
# find a patient by name, gender
async def search_patients(
    db: AsyncSession,
    name: Optional[str] = None,
    gender: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
):
    query = select(Patient)
    if name:
        query = query.where(Patient.name.ilike(f"%{name}%"))
    if gender:
        query = query.where(Patient.gender == gender)

    result = await db.execute(query.offset(skip).limit(limit))
    return result.scalars().all()

