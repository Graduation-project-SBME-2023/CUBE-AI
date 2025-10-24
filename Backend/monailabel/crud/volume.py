from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from models.volume import Volume
from schemas.volume import VolumeCreate

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
