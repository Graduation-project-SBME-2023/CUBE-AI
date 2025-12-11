from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

import crud, schemas
from core.database import get_db
from schemas.patient import PatientCreate, PatientUpdate, Patient
from datetime import date
from uuid import UUID

router = APIRouter(prefix="/patients", tags=["patients"])

def calculate_age(dob: date) -> int:
    if not dob:
        return 0
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

@router.post("/", response_model=schemas.Patient)
async def create_patient(patient: schemas.PatientCreate, db: Session = Depends(get_db)):
    try:
        return await crud.create_patient(db, patient)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/", response_model=list[schemas.Patient])
async def read_patients(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    patients = await crud.get_patients(db, skip=skip, limit=limit)
    for p in patients:
        p.age = calculate_age(p.birth_date)
    return patients

@router.get("/search", response_model=list[schemas.Patient])
async def search_patients(name: str | None = None, gender: str | None = None, db: Session = Depends(get_db)):
    patients= await crud.search_patients(db, name, gender)
    name = name.strip().replace('"', '') if name else None

    for p in patients:
        if p.birth_date:
            p.age = calculate_age(p.birth_date)
    return patients

@router.get("/{patient_id}", response_model=schemas.Patient)
async def read_patient(patient_id: UUID, db: Session = Depends(get_db)):
    p = await crud.get_patient(db, patient_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    if p.birth_date:
        p.age = calculate_age(p.birth_date)
    return p


@router.put("/{patient_id}", response_model=schemas.Patient)
async def update_patient(patient_id: UUID, patient: PatientUpdate, db: Session = Depends(get_db)):
    updated=  await crud.update_patient(db, patient_id, patient)
    if updated is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return updated

@router.delete("/{patient_id}")
async def delete_patient(patient_id: UUID, db: Session = Depends(get_db)):
    success = await crud.delete_patient(db, patient_id)
    if not success:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {"detail": "Patient deleted successfully"}

