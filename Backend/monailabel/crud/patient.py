from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from models.patient import Patient
from schemas.patient import PatientCreate, PatientUpdate
import schemas
from typing import Optional

async def get_patient(db: AsyncSession, patient_id: int):
    result = await db.execute(
        select(Patient).where(Patient.id == patient_id)
    )
    return result.scalar_one_or_none()

async def get_patients(db: AsyncSession, skip: int = 0, limit: int = 100):
    result = await db.execute(select(Patient).offset(skip).limit(limit))
    return result.scalars().all()

async def create_patient(db: AsyncSession, patient: PatientCreate):
    db_patient = Patient(**patient.dict())
    db.add(db_patient)
    await db.commit()
    await db.refresh(db_patient)
    return db_patient

async def update_patient(db: AsyncSession, patient_id: int, data: PatientUpdate):
    result = await db.execute(select(Patient).where(Patient.id == patient_id))
    patient = result.scalar_one_or_none()
    if not patient:
        return None
    for field, value in data.dict(exclude_unset=True).items():
        setattr(patient, field, value)
    await db.commit()
    await db.refresh(patient)
    return patient

async def delete_patient(db: AsyncSession, patient_id: int):
    result = await db.execute(select(Patient).where(Patient.id == patient_id))
    patient = result.scalar_one_or_none()
    if not patient:
        return False
    await db.delete(patient)
    await db.commit()
    return True

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

