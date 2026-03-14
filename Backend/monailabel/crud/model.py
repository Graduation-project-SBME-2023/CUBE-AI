from sqlalchemy.dialects.postgresql import insert
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



# async def upsert_model(db: AsyncSession, model_data: dict):
#     """
#     Insert or update a model by model_name.
#     Used by the sync endpoint.
#     """
#     stmt = insert(Model).values(**model_data)
#     stmt = stmt.on_conflict_do_update(
#         index_elements=["name"],   # unique column
#         set_={
#             "type": stmt.excluded.type,
#             "labels": stmt.excluded.labels,
#             "dimension": stmt.excluded.dimension,
#             "description": stmt.excluded.description,
#             "config": stmt.excluded.config,
#         }
#     )
#     await db.execute(stmt)
#     await db.commit()

async def upsert_model(db: AsyncSession, model_data: dict):
    result = await db.execute(
        select(Model).where(Model.name == model_data["name"])
    )
    existing = result.scalar_one_or_none()

    if not existing:
        # Insert new
        db.add(Model(**model_data))
        await db.commit()
        return "inserted"

    # Check if anything actually changed
    changed = any(
        getattr(existing, key) != model_data.get(key)
        for key in ["type", "labels", "dimension", "description", "config"]
    )

    if changed:
        for key, value in model_data.items():
            setattr(existing, key, value)
        await db.commit()
        return "updated"

    return "unchanged"