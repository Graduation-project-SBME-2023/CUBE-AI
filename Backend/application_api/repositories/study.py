import logging
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.future import select
from application_api.db.models.study import Study
from application_api.db.models.volume import Volume
from application_api.db.models.segmentation import Segmentation
from application_api.schemas.study import StudyCreate, StudyUpdate
from fastapi import HTTPException
from application_api.services.orthanc_client import cleanup_orthanc_resources_after_db_delete

logger = logging.getLogger(__name__)

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
    """Delete study from both the database and Orthanc. Returns (success: bool, error_message: str | None)"""
    result = await db.execute(select(Study).where(Study.study_uid == study_uid))
    study = result.scalar_one_or_none()

    if not study:
        return False, "Study not found"

    orthanc_study_id = study.orthanc_study_id

    volume_result = await db.execute(
        select(Volume.volume_series_instance_uid, Volume.orthanc_series_id)
        .where(Volume.study_uid == study_uid)
    )
    volumes = volume_result.all()
    volume_orthanc_ids = [row.orthanc_series_id for row in volumes]

    seg_orthanc_ids: list[str | None] = []
    if volumes:
        volume_uids = [row.volume_series_instance_uid for row in volumes]
        seg_result = await db.execute(
            select(Segmentation.orthanc_series_id).where(
                Segmentation.volume_series_instance_uid.in_(volume_uids)
            )
        )
        seg_orthanc_ids = [row[0] for row in seg_result.all()]

    if volumes:
        return False, (
            f"Study {study_uid} still has {len(volumes)} series under it. Deleting "
            "it would destroy the masks and radiomics rows derived from them; mark "
            "it archive_status='unavailable' instead, or delete the series first."
        )

    try:
        await db.delete(study)
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        logger.error("Refusing to delete referenced study %s: %s", study_uid, e)
        return False, (
            f"Study {study_uid} is still referenced by other records and cannot be "
            "deleted."
        )
    except SQLAlchemyError as e:
        await db.rollback()
        error_msg = f"Database error: {str(e)}"
        logger.error(error_msg)
        return False, error_msg

    await cleanup_orthanc_resources_after_db_delete(
        "series",
        [*volume_orthanc_ids, *seg_orthanc_ids],
        context=f"DB delete of study {study_uid}",
    )
    await cleanup_orthanc_resources_after_db_delete(
        "studies",
        [orthanc_study_id],
        context=f"DB delete of study {study_uid}",
    )
    return True, None

async def get_studies_by_patient(
    db: AsyncSession, patient_id: UUID, skip: int = 0, limit: int = 100
):
    try:
        result = await db.execute(
            select(Study).where(Study.patient_id == patient_id).offset(skip).limit(limit)
        )
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    
async def search_studies(
    db: AsyncSession,
    modality: str | None = None,
    keyword: str | None = None,
    skip: int = 0,
    limit: int = 100,
):
    try:
        query = select(Study)
        if modality:
            query = query.where(Study.modality.ilike(f"%{modality}%"))
        if keyword:
            query = query.where(Study.description.ilike(f"%{keyword}%"))
        result = await db.execute(query.offset(skip).limit(limit))
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
