from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError
from models.patient import Patient
from schemas.patient import PatientCreate, PatientUpdate
import schemas
from typing import Optional
from datetime import datetime, date
from typing import Optional, Union

from datetime import datetime
from typing import Optional

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

async def get_patient(db: AsyncSession, patient_id: int):
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

async def update_patient(db: AsyncSession, patient_id: int, data: PatientUpdate):
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
    

async def delete_patient(db: AsyncSession, patient_id: int):
    result = await db.execute(select(Patient).where(Patient.patient_id == patient_id))
    patient = result.scalar_one_or_none()
    if not patient:
        return False
    try:
        await db.delete(patient)
        await db.commit()
        return True
    except SQLAlchemyError:
        await db.rollback()
        return False

# ---------------------------
# find a patient by name, gender
async def search_patients(db: AsyncSession, name: Optional[str] = None, gender: Optional[str] = None, age: Optional[int] = None):
    query = select(Patient)
    if name:
        query = query.where(Patient.name.ilike(f"%{name}%"))
    if gender:
        query = query.where(Patient.gender == gender)

    result = await db.execute(query)
    return result.scalars().all()

