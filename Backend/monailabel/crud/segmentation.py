from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from typing import List, Optional, Dict, Any

from models.segmentation import Segmentation
from schemas.segmentation import SegmentationCreate, SegmentationBase, SegmentationUpdate

# Get by label UID (primary key)
async def get_segmentation(db: AsyncSession, label_uid: str) -> Optional[Segmentation]:
    result = await db.execute(
        select(Segmentation).where(Segmentation.label_series_instance_uid == label_uid)
    )
    return result.scalar_one_or_none()


# List all (paginated)
async def get_segmentations(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[Segmentation]:
    result = await db.execute(select(Segmentation).offset(skip).limit(limit))
    return result.scalars().all()


# Create
async def create_segmentation(db: AsyncSession, segmentation: SegmentationCreate) -> Segmentation:
    db_seg = Segmentation(**segmentation.dict())
    db.add(db_seg)
    await db.commit()
    await db.refresh(db_seg)
    return db_seg


async def update_segmentation(
    db: AsyncSession, segmentation_id: int, segmentation_update: SegmentationUpdate
):
    db_segmentation = await get_segmentation(db, segmentation_id)
    if not db_segmentation:
        return None

    update_data = segmentation_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_segmentation, key, value)

    await db.commit()
    await db.refresh(db_segmentation)
    return db_segmentation


# Delete
async def delete_segmentation(db: AsyncSession, label_uid: str) -> bool:
    seg = await get_segmentation(db, label_uid)
    if not seg:
        return False
    await db.delete(seg)
    await db.commit()
    return True


# Get by volume UID
async def get_segmentations_by_volume(db: AsyncSession, volume_uid: str) -> List[Segmentation]:
    result = await db.execute(
        select(Segmentation).where(Segmentation.volume_series_instance_uid == volume_uid)
    )
    return result.scalars().all()


# Get by model_name
async def get_segmentations_by_model(db: AsyncSession, model_name: str) -> List[Segmentation]:
    result = await db.execute(
        select(Segmentation).where(Segmentation.model_name == model_name)
    )
    return result.scalars().all()


# Get unvalidated: no model_name assigned (candidate for review)
async def get_unvalidated_segmentations(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[Segmentation]:
    result = await db.execute(
        select(Segmentation).where(Segmentation.model_name == None).offset(skip).limit(limit)
    )
    return result.scalars().all()


# Search / filter by type or method
async def search_segmentations(
    db: AsyncSession,
    segmentation_type: Optional[str] = None,
    method: Optional[str] = None,
    skip: int = 0,
    limit: int = 100
) -> List[Segmentation]:
    query = select(Segmentation)
    if segmentation_type:
        query = query.where(Segmentation.segmentation_type.ilike(f"%{segmentation_type}%"))
    if method:
        query = query.where(Segmentation.method.ilike(f"%{method}%"))
    result = await db.execute(query.offset(skip).limit(limit))
    return result.scalars().all()


# Simple statistics: count per model_name, count per segmentation_type
async def get_segmentation_stats(db: AsyncSession):
    result = await db.execute(
        select(
            Segmentation.model_name,
            func.count(Segmentation.label_series_instance_uid)
        ).group_by(Segmentation.model_name)
    )
    counts_by_model = [{"model_name": m, "count": c} for m, c in result.all()]

    result2 = await db.execute(
        select(
            Segmentation.segmentation_type,
            func.count(Segmentation.label_series_instance_uid)
        ).group_by(Segmentation.segmentation_type)
    )
    counts_by_type = [{"segmentation_type": t, "count": c} for t, c in result2.all()]

    return {"counts_by_model": counts_by_model, "counts_by_type": counts_by_type}