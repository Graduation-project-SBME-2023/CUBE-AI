from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from models.patient import Patient
from schemas.patient import PatientCreate
import schemas

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