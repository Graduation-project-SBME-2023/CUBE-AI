from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from models.segmentation import Segmentation
from schemas.segmentation import SegmentationCreate

async def get_segmentation(db: AsyncSession, segmentation_id: int):
    result = await db.execute(
        select(Segmentation).where(Segmentation.id == segmentation_id)
    )
    return result.scalar_one_or_none()

async def get_segmentations(db: AsyncSession, skip: int = 0, limit: int = 100):
    result = await db.execute(select(Segmentation).offset(skip).limit(limit))
    return result.scalars().all()

async def create_segmentation(db: AsyncSession, segmentation: SegmentationCreate):
    db_segmentation = Segmentation(**segmentation.dict())
    db.add(db_segmentation)
    await db.commit()
    await db.refresh(db_segmentation)
    return db_segmentation
