from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.future import select
from models.study import Study
from schemas.study import StudyCreate, StudyUpdate
from fastapi import HTTPException
async def get_study(db: AsyncSession, study_uid: str):
    try:
        result = await db.execute(select(Study).where(Study.study_uid == study_uid))
        study = result.scalar_one_or_none()
        if not study:
            raise HTTPException(status_code=404, detail=f"Study {study_uid} not found")
        return study
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    

async def get_studies(db: AsyncSession, skip: int = 0, limit: int = 100):
    try:
        result = await db.execute(select(Study).offset(skip).limit(limit))
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def create_study(db: AsyncSession, study: StudyCreate):
    db_study = Study(**study.dict())
    db.add(db_study)
    try:
        await db.commit()
        await db.refresh(db_study)
        return db_study
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

async def update_study(db: AsyncSession, study_uid: str, updates: StudyUpdate):
    try:
        result = await db.execute(select(Study).where(Study.study_uid == study_uid))
        db_study = result.scalar_one_or_none()
        if not db_study:
            raise HTTPException(status_code=404, detail=f"Study {study_uid} not found")
        
        for key, value in updates.dict(exclude_unset=True).items():
            setattr(db_study, key, value)

        await db.commit()
        await db.refresh(db_study)
        return db_study
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to update study: {str(e)}")

async def delete_study(db: AsyncSession, study_uid: str):
    try:
        result = await db.execute(select(Study).where(Study.study_uid == study_uid))
        db_study = result.scalar_one_or_none()
        if not db_study:
            raise HTTPException(status_code=404, detail=f"Study {study_uid} not found")

        await db.delete(db_study)
        await db.commit()
        return {"detail": f"Study {study_uid} deleted successfully"}
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to delete study: {str(e)}")

async def get_studies_by_patient(db: AsyncSession, patient_id: int):
    try:
        result = await db.execute(select(Study).where(Study.patient_id == patient_id))
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    
async def search_studies(db: AsyncSession, modality: str | None = None, keyword: str | None = None):
    try:
        query = select(Study)
        if modality:
            query = query.where(Study.modality.ilike(f"%{modality}%"))
        if keyword:
            query = query.where(Study.description.ilike(f"%{keyword}%"))
        result = await db.execute(query)
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")