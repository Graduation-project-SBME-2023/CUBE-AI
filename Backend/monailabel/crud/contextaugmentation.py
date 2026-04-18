# app/crud/context_augmentation.py
from fastapi import HTTPException
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from models.contextaugmentation import ContextAugmentation
from schemas.contextaugmentation import ContextAugmentationCreate, ContextAugmentationUpdate


async def create_context_augmentation(db: AsyncSession, data: ContextAugmentationCreate) -> ContextAugmentation:
    try:
        obj = ContextAugmentation(id=uuid4(), **data.model_dump())
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return obj
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def get_context_augmentation(db: AsyncSession, ca_id: UUID) -> ContextAugmentation:
    try:
        result = await db.execute(
            select(ContextAugmentation).where(ContextAugmentation.id == ca_id)
        )
        obj = result.scalar_one_or_none()
        if not obj:
            raise HTTPException(status_code=404, detail="ContextAugmentation not found")
        return obj
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def update_context_augmentation(
    db: AsyncSession, ca_id: UUID, data: ContextAugmentationUpdate
) -> ContextAugmentation:
    obj = await get_context_augmentation(db, ca_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    try:
        await db.commit()
        await db.refresh(obj)
        return obj
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


async def delete_context_augmentation(db: AsyncSession, ca_id: UUID) -> dict:
    obj = await get_context_augmentation(db, ca_id)
    try:
        await db.delete(obj)
        await db.commit()
        return {"detail": "ContextAugmentation deleted"}
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")