from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from schemas.radiomics import Radiomics, RadiomicsCreate
from crud.radiomics import get_radiomics, get_radiomics_list, create_radiomics
from core.database import get_db

router = APIRouter(prefix="/radiomics", tags=["radiomics"])

@router.get("/{radiomics_id}", response_model=Radiomics)
async def read_radiomics(radiomics_id: str, db: Session = Depends(get_db)):
    db_radiomics = await get_radiomics(db, radiomics_id=radiomics_id)
    return db_radiomics

@router.get("/", response_model=list[Radiomics])
async def read_radiomics_list(skip: int = 0, limit: int = 100,
                              db: Session = Depends(get_db)):
    radiomics_list = await get_radiomics_list(db, skip=skip, limit=limit)
    return radiomics_list

@router.post("/", response_model=Radiomics)
async def create_radiomics_endpoint(radiomics: RadiomicsCreate, 
                                    db: Session = Depends(get_db)):
    return await create_radiomics(db=db, radiomics=radiomics)
