from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from models.volume import Volume
from schemas.volume import VolumeCreate, VolumeUpdate
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException
from sqlalchemy import func


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
        raise HTTPException(status_code=400, detail=str(e))

async def update_volume(db: AsyncSession, volume_series_instance_uid: str, updates: VolumeUpdate):
    
    result = await db.execute(select(Volume).where(Volume.volume_series_instance_uid == volume_series_instance_uid))
    db_volume = result.scalar_one_or_none()
    if not db_volume:
        raise HTTPException(status_code=404, detail= f"Volume {volume_series_instance_uid} not found")
    print("Before:", db_volume.__dict__)
    for key, value in updates.dict(exclude_unset=True).items():
        setattr(db_volume, key, value)
    print("After:", db_volume.__dict__)
    try:    
        await db.commit()
        await db.refresh(db_volume)
        return db_volume
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to update volume: {str(e)}")

async def delete_volume(db: AsyncSession, volume_series_instance_uid: str):
    try:
        result = await db.execute(select(Volume).where(Volume.volume_series_instance_uid == volume_series_instance_uid))
        db_volume = result.scalar_one_or_none()
        if not db_volume:
            raise HTTPException(status_code=404, detail= f"Volume {volume_series_instance_uid} not found")
        await db.delete(db_volume)
        await db.commit()
        return {"detail": f"Volume {volume_series_instance_uid} deleted successfully"}
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to delete volume: {str(e)}")

async def get_volumes_by_study(db: AsyncSession, study_uid: str):
    try:
        result = await db.execute(select(Volume).where(Volume.study_uid == study_uid))
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def search_volumes(db: AsyncSession, modality: str | None = None, keyword: str | None = None):
    try:
        query = select(Volume)
        if modality:
            query = query.where(Volume.modality.ilike(f"%{modality}%"))
        if keyword:
            query = query.where(Volume.series_description.ilike(f"%{keyword}%"))
        result = await db.execute(query)
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def count_volumes_by_study(db: AsyncSession, study_uid: str):
    try:
        result = await db.execute(select(func.count()).select_from(Volume).where(Volume.study_uid == study_uid))
        return result.scalar()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
