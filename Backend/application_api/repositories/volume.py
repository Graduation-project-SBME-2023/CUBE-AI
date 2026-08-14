import logging

from fastapi import HTTPException
from sqlalchemy import func, not_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.volume import Volume
from application_api.schemas.volume import VolumeCreate, VolumeUpdate
from application_api.db.models.study import Study
from application_api.services.orthanc_client import delete_orthanc_resource

logger = logging.getLogger(__name__)


async def get_volume(db: AsyncSession, volume_series_instance_uid: str):
    try:
        result = await db.execute(
            select(Volume).where(Volume.volume_series_instance_uid == volume_series_instance_uid)
        )
        volume = result.scalar_one_or_none()
        if not volume:
            raise HTTPException(status_code=404, detail="Volume not found")
        return volume
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def get_volumes(db: AsyncSession, skip: int = 0, limit: int = 100):
    try:
        result = await db.execute(select(Volume).offset(skip).limit(limit))
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def create_volume(db: AsyncSession, volume: VolumeCreate):
    db_volume = Volume(**volume.dict())
    db.add(db_volume)
    try:
        await db.commit()
        await db.refresh(db_volume)
        return db_volume
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

async def update_volume(db: AsyncSession, volume_series_instance_uid: str, updates: VolumeUpdate):
    
    result = await db.execute(select(Volume).where(Volume.volume_series_instance_uid == volume_series_instance_uid))
    db_volume = result.scalar_one_or_none()
    if not db_volume:
        raise HTTPException(status_code=404, detail= f"Volume {volume_series_instance_uid} not found")

    for key, value in updates.dict(exclude_unset=True).items():
        setattr(db_volume, key, value)

    try:    
        await db.commit()
        await db.refresh(db_volume)
        return db_volume
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to update volume: {str(e)}")

async def delete_volume(db: AsyncSession, volume_uid: str):
    """
    Delete volume from both Orthanc and database.
    Returns (success: bool, error_message: str | None)
    """
    result = await db.execute(
        select(Volume).where(Volume.volume_series_instance_uid == volume_uid)
    )
    volume = result.scalar_one_or_none()
    
    if not volume:
        return False, "Volume not found"
    
    orthanc_series_id = volume.orthanc_series_id
    deleted_from_orthanc, error_msg = await delete_orthanc_resource(
        "series",
        orthanc_series_id,
    )
    if not deleted_from_orthanc:
        return False, error_msg
    
    # Delete from database
    try:
        await db.delete(volume)
        await db.commit()
        return True, None
    except SQLAlchemyError as e:
        await db.rollback()
        error_msg = f"Database error: {str(e)}"
        logger.error(error_msg)
        return False, error_msg

async def get_volumes_by_study(
    db: AsyncSession, study_uid: str, skip: int = 0, limit: int = 100
):
    try:
        result = await db.execute(
            select(Volume).where(Volume.study_uid == study_uid).offset(skip).limit(limit)
        )
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def search_volumes(
    db: AsyncSession,
    modality: str | None = None,
    keyword: str | None = None,
    skip: int = 0,
    limit: int = 100,
):
    try:
        query = select(Volume)
        if modality:
            query = query.where(Volume.modality.ilike(f"%{modality}%"))
        if keyword:
            query = query.where(Volume.series_description.ilike(f"%{keyword}%"))
        result = await db.execute(query.offset(skip).limit(limit))
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    
async def search_volumes_by_description(
    db: AsyncSession,
    words_to_include: list[str],
    words_to_exclude: list[str] | None = None,
    skip: int = 0,
    limit: int = 100,
):
    """
    Search volumes by keywords in series_description AND body_part_examined.
    Returns a list of dicts with uid, series_description, body_part_examined,
    and modality so the caller can see what anatomy each volume covers.
    """
    try:
        if not words_to_include:
            return []

        query = select(
            Volume.volume_series_instance_uid,
            Volume.series_description,
            Volume.body_part_examined,
            Volume.modality,
        )
        # Search both series_description and body_part_examined for each keyword
        include_conditions = [
            or_(
                Volume.series_description.ilike(f"%{word}%"),
                Volume.body_part_examined.ilike(f"%{word}%"),
            )
            for word in words_to_include
        ]
        query = query.where(or_(*include_conditions))
        if words_to_exclude:
            exclude_conditions = [
                or_(
                    Volume.series_description.ilike(f"%{word}%"),
                    Volume.body_part_examined.ilike(f"%{word}%"),
                )
                for word in words_to_exclude
            ]
            query = query.where(not_(or_(*exclude_conditions)))
        results = await db.execute(query.offset(skip).limit(limit))
        rows = results.all()
        return [
            {
                "uid": row.volume_series_instance_uid,
                "series_description": row.series_description or "",
                "body_part_examined": row.body_part_examined or "",
                "modality": row.modality or "",
            }
            for row in rows
        ]
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500,detail=f"Database error: {str(e)}")

async def count_volumes_by_study(db: AsyncSession, study_uid: str):
    try:
        result = await db.execute(select(func.count()).select_from(Volume).where(Volume.study_uid == study_uid))
        return result.scalar()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def get_study_by_volume(db: AsyncSession, volume_series_instance_uid: str):
    try:
        # First, get the volume
        result = await db.execute(
            select(Volume).where(Volume.volume_series_instance_uid == volume_series_instance_uid)
        )
        volume = result.scalar_one_or_none()
        if not volume:
            raise HTTPException(status_code=404, detail=f"Volume {volume_series_instance_uid} not found")

        # Then, get the study associated with that volume
        result = await db.execute(
            select(Study).where(Study.study_uid == volume.study_uid)
        )
        study = result.scalar_one_or_none()
        if not study:
            raise HTTPException(status_code=404, detail=f"Study {volume.study_uid} not found")

        return study

    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def get_unique_series_descriptions(
    db: AsyncSession, skip: int = 0, limit: int = 100
) -> list[str]:
    try:
        # Select distinct descriptions and ignore any NULL values
        query = (
            select(Volume.series_description)
            .distinct()
            .where(Volume.series_description.is_not(None))
            .order_by(Volume.series_description)
            .offset(skip)
            .limit(limit)
        )
        
        results = await db.execute(query)
        
        # scalars().all() flattens the result into a simple list of strings
        unique_descriptions = results.scalars().all()
        return unique_descriptions
        
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
