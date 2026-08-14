from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import AsyncSession

from application_api import repositories, schemas
from application_api.db.session import get_db
from application_api.schemas.patient import PatientCreate, PatientUpdate, Patient
from datetime import date
from uuid import UUID

router = APIRouter(prefix="/patients", tags=["patients"])

def calculate_age(dob: date) -> int:
    if not dob:
        return 0
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

@router.post("/", response_model=schemas.Patient)
async def create_patient(patient: schemas.PatientCreate, db: AsyncSession = Depends(get_db)):
    try:
        return await repositories.create_patient(db, patient)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/", response_model=list[schemas.Patient])
async def read_patients(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    patients = await repositories.get_patients(db, skip=skip, limit=limit)
    for p in patients:
        p.age = calculate_age(p.birth_date)
    return patients

@router.get("/search", response_model=list[schemas.Patient])
async def search_patients(
    name: str | None = None,
    gender: str | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    patients = await repositories.search_patients(db, name, gender, skip=skip, limit=limit)
    name = name.strip().replace('"', '') if name else None

    for p in patients:
        if p.birth_date:
            p.age = calculate_age(p.birth_date)
    return patients

@router.get("/{patient_id}", response_model=schemas.Patient)
async def read_patient(patient_id: UUID, db: AsyncSession = Depends(get_db)):
    p = await repositories.get_patient(db, patient_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    if p.birth_date:
        p.age = calculate_age(p.birth_date)
    return p


@router.put("/{patient_id}", response_model=schemas.Patient)
async def update_patient(patient_id: UUID, patient: PatientUpdate, db: AsyncSession = Depends(get_db)):
    updated=  await repositories.update_patient(db, patient_id, patient)
    if updated is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return updated

@router.delete("/{patient_id}")
async def delete_patient(patient_id: UUID, db: AsyncSession = Depends(get_db)):
    """
    Delete patient from both Orthanc and database.
    This will cascade delete all studies, volumes, and segmentations.
    """
    success, error_message = await repositories.delete_patient(db, patient_id)
    
    if not success:
        if error_message == "Patient not found":
            raise HTTPException(status_code=404, detail=error_message)
        else:
            raise HTTPException(status_code=500, detail=error_message)
    
    return {"detail": "Patient deleted successfully"}
