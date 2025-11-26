from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from typing import List, Optional, Dict, Any

from models.radiomics import Radiomics
from schemas.radiomics import RadiomicsCreate
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException

# ----------------------------------------------------------------------
# Get by ID
# ----------------------------------------------------------------------
async def get_radiomics(db: AsyncSession, radiomics_id: int) -> Optional[Radiomics]:
    try:
        result = await db.execute(
            select(Radiomics).where(Radiomics.id == radiomics_id)
        )
        radiomic = result.scalar_one_or_none()
        if not radiomic:
            raise HTTPException(status_code=404, detail=f"Radiomics {radiomics_id} not found")
        return radiomic
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# ----------------------------------------------------------------------
# Get by label UID
# ----------------------------------------------------------------------
async def get_radiomics_by_label(db: AsyncSession, label_uid: str) -> Optional[Radiomics]:
    try:
        result = await db.execute(
            select(Radiomics).where(Radiomics.label_series_instance_uid == label_uid)
        )
        radiomic = result.scalar_one_or_none()
        if not radiomic:
            raise HTTPException(status_code=404, detail=f"Radiomics with label UID {label_uid} not found")
        return radiomic
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# ----------------------------------------------------------------------
# List all (paginated)
# ----------------------------------------------------------------------
async def get_radiomics_list(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[Radiomics]:
    try:
        result = await db.execute(select(Radiomics).offset(skip).limit(limit))
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# ----------------------------------------------------------------------
# Create
# ----------------------------------------------------------------------
async def create_radiomics(db: AsyncSession, radiomics: RadiomicsCreate) -> Radiomics:
    db_radiomics = Radiomics(**radiomics.dict())
    db.add(db_radiomics)
    try:
        await db.commit()
        await db.refresh(db_radiomics)
        return db_radiomics
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to create radiomics: {str(e)}")



# ----------------------------------------------------------------------
# Delete
# ----------------------------------------------------------------------
async def delete_radiomics(db: AsyncSession, radiomics_id: int) -> bool:
    try:
        db_radiomics = await get_radiomics(db, radiomics_id)
        if not db_radiomics:
            raise HTTPException(status_code=404, detail=f"Radiomics {radiomics_id} not found")
        await db.delete(db_radiomics)
        await db.commit()
        return {"detail": f"Radiomics {radiomics_id} deleted successfully"}
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to delete radiomics: {str(e)}")


from sqlalchemy import func, cast, String
from sqlalchemy.dialects.postgresql import JSONB

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import cast, String, select, func

async def search_radiomics(
    db: AsyncSession,
    feature_key: Optional[str] = None,
    feature_value: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> list[Radiomics]:
    """
    Search radiomics entries containing a specific feature key/value.
    Works with PostgreSQL JSONB.
    """
    try:
        query = select(Radiomics)

        if feature_key:
            # Use PostgreSQL JSONB key existence operator '?'
            query = query.where(Radiomics.features.op("?")(feature_key))

        if feature_value and feature_key:
            # Compare value (cast to text first)
            query = query.where(
                cast(Radiomics.features[feature_key], String).ilike(f"%{feature_value}%")
            )

        result = await db.execute(query.offset(skip).limit(limit))
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
