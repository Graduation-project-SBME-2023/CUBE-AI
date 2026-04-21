# app/routers/context_augmentation.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from core.database import get_db
from crud.contextaugmentation import (
    create_context_augmentation,
    get_context_augmentation,
    update_context_augmentation,
    delete_context_augmentation,
)
from schemas.contextaugmentation import (
    ContextAugmentationCreate,
    ContextAugmentationUpdate,
    ContextAugmentationRead,
)

router = APIRouter(prefix="/context-augmentations", tags=["Context Augmentations"])


@router.post("/", response_model=ContextAugmentationRead, status_code=201)
async def create(data: ContextAugmentationCreate, db: AsyncSession = Depends(get_db)):
    return await create_context_augmentation(db, data)


@router.get("/{ca_id}", response_model=ContextAugmentationRead)
async def read(ca_id: UUID, db: AsyncSession = Depends(get_db)):
    return await get_context_augmentation(db, ca_id)


@router.patch("/{ca_id}", response_model=ContextAugmentationRead)
async def update(ca_id: UUID, data: ContextAugmentationUpdate, db: AsyncSession = Depends(get_db)):
    return await update_context_augmentation(db, ca_id, data)


@router.delete("/{ca_id}")
async def delete(ca_id: UUID, db: AsyncSession = Depends(get_db)):
    return await delete_context_augmentation(db, ca_id)