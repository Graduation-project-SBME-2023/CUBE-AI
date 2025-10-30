from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from models.volume import Volume
from schemas.volume import VolumeCreate, VolumeUpdate

async def get_volume(db: AsyncSession, volume_series_instance_uid: str):
    result = await db.execute(
        select(Volume).where(Volume.volume_series_instance_uid == volume_series_instance_uid)
    )
    return result.scalar_one_or_none()

async def get_volumes(db: AsyncSession, skip: int = 0, limit: int = 100):
    result = await db.execute(select(Volume).offset(skip).limit(limit))
    return result.scalars().all()

async def create_volume(db: AsyncSession, volume: VolumeCreate):
    db_volume = Volume(**volume.dict())
    db.add(db_volume)
    await db.commit()
    await db.refresh(db_volume)
    return db_volume

async def update_volume(db: AsyncSession, volume_series_instance_uid: str, updates: VolumeUpdate):
    result = await db.execute(select(Volume).where(Volume.volume_series_instance_uid == volume_series_instance_uid))
    db_volume = result.scalar_one_or_none()
    if not db_volume:
        return None
    for key, value in updates.dict(exclude_unset=True).items():
        setattr(db_volume, key, value)
    await db.commit()
    await db.refresh(db_volume)
    return db_volume

async def delete_volume(db: AsyncSession, volume_series_instance_uid: str):
    result = await db.execute(select(Volume).where(Volume.volume_series_instance_uid == volume_series_instance_uid))
    db_volume = result.scalar_one_or_none()
    if not db_volume:
        return None
    await db.delete(db_volume)
    await db.commit()
    return db_volume

async def get_volumes_by_study(db: AsyncSession, study_uid: str):
    result = await db.execute(select(Volume).where(Volume.study_uid == study_uid))
    return result.scalars().all()

async def search_volumes(db: AsyncSession, modality: str | None = None, keyword: str | None = None):
    query = select(Volume)
    if modality:
        query = query.where(Volume.modality.ilike(f"%{modality}%"))
    if keyword:
        query = query.where(Volume.series_description.ilike(f"%{keyword}%"))
    result = await db.execute(query)
    return result.scalars().all()

from sqlalchemy import func

async def count_volumes_by_study(db: AsyncSession, study_uid: str):
    result = await db.execute(select(func.count()).select_from(Volume).where(Volume.study_uid == study_uid))
    return result.scalar()
