from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from models.model import Model
from schemas.model import ModelCreate
from uuid import UUID
async def get_model(db: AsyncSession, model_id: UUID):
    result = await db.execute(
        select(Model).where(Model.model_id == model_id)
    )
    return result.scalar_one_or_none()

async def get_models(db: AsyncSession, skip: int = 0, limit: int = 100):
    result = await db.execute(select(Model).offset(skip).limit(limit))
    return result.scalars().all()

async def create_model(db: AsyncSession, model: ModelCreate):
    db_model = Model(**model.dict())
    db.add(db_model)
    await db.commit()
    await db.refresh(db_model)
    return db_model

async def update_model(db: AsyncSession, model_id: UUID, updates: dict):
    result = await db.execute(
        select(Model).where(Model.model_id == model_id)
    )
    db_model = result.scalar_one_or_none()
    if not db_model:
        return None
    for key, value in updates.items():
        setattr(db_model, key, value)
    await db.commit()
    await db.refresh(db_model)
    return db_model


async def delete_model(db: AsyncSession, model_id: UUID):
    result = await db.execute(
        select(Model).where(Model.model_id == model_id)
    )
    db_model = result.scalar_one_or_none()
    if db_model:
        await db.delete(db_model)
        await db.commit()
    return {"detail": "Model deleted"}

