from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from schemas.study import StudyRead, StudyCreate, StudyUpdate
from crud.study import delete_study, get_studies_by_patient, get_study, get_studies, create_study, search_studies, search_studies, update_study
from core.database import get_db
from uuid import UUID
router = APIRouter(prefix="/studies", tags=["studies"])

@router.get("/{study_uid}", response_model=StudyRead)
async def read_study(study_uid: str, db: Session = Depends(get_db)):
    db_study = await get_study(db, study_uid=study_uid)
    return db_study

@router.get("/", response_model=list[StudyRead])
async def read_studies(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    studies = await get_studies(db, skip=skip, limit=limit)
    return studies

@router.post("/", response_model=StudyRead)
async def create_study_endpoint(study: StudyCreate, db: Session = Depends(get_db)):
    return await create_study(db=db, study=study)

@router.put("/{study_uid}", response_model=StudyRead)
async def update_study_endpoint(
    study_uid: str,
    updates: StudyUpdate,
    db: Session = Depends(get_db)
):
    updated_study = await update_study(db, study_uid, updates)
    return updated_study

@router.delete("/{study_uid}")
async def delete_study_endpoint(study_uid: str, db: Session = Depends(get_db)):
    deleted_study = await delete_study(db, study_uid)
    return deleted_study

@router.get("/search/", response_model=list[StudyRead])
async def search_studies_endpoint(
    modality: str | None = None,
    keyword: str | None = None,
    db: Session = Depends(get_db)
):
    return await search_studies(db, modality, keyword)
@router.get("/by_patient/{patient_id}", response_model=list[StudyRead])
async def read_studies_by_patient(patient_id: UUID, db: Session = Depends(get_db)):
    studies = await get_studies_by_patient(db, patient_id)
    return studies


