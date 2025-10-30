from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from typing import List, Optional, Dict, Any

from models.radiomics import Radiomics
from schemas.radiomics import RadiomicsCreate


# ----------------------------------------------------------------------
# Get by ID
# ----------------------------------------------------------------------
async def get_radiomics(db: AsyncSession, radiomics_id: int) -> Optional[Radiomics]:
    result = await db.execute(
        select(Radiomics).where(Radiomics.id == radiomics_id)
    )
    return result.scalar_one_or_none()


# ----------------------------------------------------------------------
# Get by label UID
# ----------------------------------------------------------------------
async def get_radiomics_by_label(db: AsyncSession, label_uid: str) -> Optional[Radiomics]:
    result = await db.execute(
        select(Radiomics).where(Radiomics.label_series_instance_uid == label_uid)
    )
    return result.scalar_one_or_none()


# ----------------------------------------------------------------------
# List all (paginated)
# ----------------------------------------------------------------------
async def get_radiomics_list(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[Radiomics]:
    result = await db.execute(select(Radiomics).offset(skip).limit(limit))
    return result.scalars().all()


# ----------------------------------------------------------------------
# Create
# ----------------------------------------------------------------------
async def create_radiomics(db: AsyncSession, radiomics: RadiomicsCreate) -> Radiomics:
    db_radiomics = Radiomics(**radiomics.dict())
    db.add(db_radiomics)
    await db.commit()
    await db.refresh(db_radiomics)
    return db_radiomics




# ----------------------------------------------------------------------
# Delete
# ----------------------------------------------------------------------
async def delete_radiomics(db: AsyncSession, radiomics_id: int) -> bool:
    db_radiomics = await get_radiomics(db, radiomics_id)
    if not db_radiomics:
        return False
    await db.delete(db_radiomics)
    await db.commit()
    return True


# ----------------------------------------------------------------------
# Search / Filter (by feature key or value)
# ----------------------------------------------------------------------
async def search_radiomics(
    db: AsyncSession,
    feature_key: Optional[str] = None,
    feature_value: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> List[Radiomics]:
    """
    Simple search for radiomics entries containing a specific feature key/value.
    Note: Requires database JSON support (e.g. PostgreSQL JSONB)
    """
    query = select(Radiomics)
    if feature_key:
        query = query.where(Radiomics.features.has_key(feature_key))  # type: ignore
    if feature_value:
        query = query.where(
            func.cast(Radiomics.features[feature_key], func.TEXT).ilike(f"%{feature_value}%")  # type: ignore
        )
    result = await db.execute(query.offset(skip).limit(limit))
    return result.scalars().all()


