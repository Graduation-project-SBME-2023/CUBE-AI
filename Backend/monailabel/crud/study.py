from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.future import select
from models.study import Study
from schemas.study import StudyCreate, StudyUpdate, StudyRead
from fastapi import HTTPException
from uuid import UUID
from datetime import date
import aiohttp
from crud.orthanc_sync import ORTHANC_URL, AUTH

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
    """
    Delete study from both Orthanc and database.
    Returns (success: bool, error_message: str | None)
    """
    result = await db.execute(select(Study).where(Study.study_uid == study_uid))
    study = result.scalar_one_or_none()
    
    if not study:
        return False, "Study not found"
    
    orthanc_study_id = study.orthanc_study_id
    study_description = study.description
    
    # Delete from Orthanc first
    if orthanc_study_id:
        async with aiohttp.ClientSession(auth=AUTH) as session:
            try:
                async with session.delete(f"{ORTHANC_URL}/studies/{orthanc_study_id}") as resp:
                    if resp.status == 404:
                        print(f"[WARN] Study {orthanc_study_id} not found in Orthanc (already deleted)")
                    elif resp.status not in [200, 204]:
                        error_msg = f"Failed to delete from Orthanc: HTTP {resp.status}"
                        print(f"[ERROR] {error_msg}")
                        return False, error_msg
                    else:
                        print(f"[INFO] 🗑 Deleted study {study_description} from Orthanc")
            except aiohttp.ClientError as e:
                error_msg = f"Orthanc connection error: {str(e)}"
                print(f"[ERROR] {error_msg}")
                return False, error_msg
    
    # Delete from database
    try:
        await db.delete(study)
        await db.commit()
        print(f"[INFO] 🗑 Deleted study {study_description} from database")
        return True, None
    except SQLAlchemyError as e:
        await db.rollback()
        error_msg = f"Database error: {str(e)}"
        print(f"[ERROR] {error_msg}")
        return False, error_msg

async def get_studies_by_patient(db: AsyncSession, patient_id: UUID):
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