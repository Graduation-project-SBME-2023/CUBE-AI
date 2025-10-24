from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import crud, schemas
from core.database import get_db

router = APIRouter(prefix="/patients", tags=["patients"])

@router.post("/", response_model=schemas.Patient)
def create_patient(patient: schemas.PatientCreate, db: Session = Depends(get_db)):
    return crud.create_patient(db=db, patient=patient)

@router.get("/", response_model=list[schemas.Patient])
async def read_patients(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    patients = await crud.get_patients(db, skip=skip, limit=limit)
    return patients
