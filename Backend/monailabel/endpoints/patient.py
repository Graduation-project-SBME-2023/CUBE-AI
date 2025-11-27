from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import crud, schemas
from core.database import get_db
from schemas.patient import PatientCreate, PatientUpdate, Patient
from datetime import date


router = APIRouter(prefix="/patients", tags=["patients"])

def calculate_age(dob: date) -> int:
    try:
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    except AttributeError:
        return -1
    return age

@router.post("/", response_model=schemas.Patient)
def create_patient(patient: schemas.PatientCreate, db: Session = Depends(get_db)):
    return crud.create_patient(db=db, patient=patient)

@router.get("/", response_model=list[schemas.Patient])
async def read_patients(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    patients = await crud.get_patients(db, skip=skip, limit=limit)
    for p in patients:
        p.age = calculate_age(p.birth_date)
    return patients

@router.get("/{patient_id}", response_model=schemas.Patient)
async def read_patient(patient_id: int, db: Session = Depends(get_db)):
    p = await crud.get_patient(db, patient_id)
    p.age = calculate_age(p.birth_date)
    return p


@router.put("/{patient_id}", response_model=schemas.Patient)
async def update_patient(patient_id: int, patient: PatientUpdate, db: Session = Depends(get_db)):
    return await crud.update_patient(db, patient_id, patient)

@router.delete("/{patient_id}")
async def delete_patient(patient_id: int, db: Session = Depends(get_db)):
    return await crud.delete_patient(db, patient_id)

@router.get("/search", response_model=list[schemas.Patient])
async def search_patients(name: str | None = None, gender: str | None = None, db: Session = Depends(get_db)):
    return await crud.search_patients(db, name, gender)
