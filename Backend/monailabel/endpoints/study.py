from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from schemas.study import Study, StudyCreate
from crud.study import get_study, get_studies, create_study
from core.database import get_db

router = APIRouter(prefix="/studies", tags=["studies"])

@router.get("/{study_uid}", response_model=Study)
async def read_study(study_uid: str, db: Session = Depends(get_db)):
    db_study = await get_study(db, study_uid=study_uid)
    return db_study

@router.get("/", response_model=list[Study])
async def read_studies(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    studies = await get_studies(db, skip=skip, limit=limit)
    return studies

@router.post("/", response_model=Study)
async def create_study_endpoint(study: StudyCreate, db: Session = Depends(get_db)):
    return await create_study(db=db, study=study)
