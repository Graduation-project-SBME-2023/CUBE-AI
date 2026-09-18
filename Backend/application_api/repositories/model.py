from sqlalchemy import literal_column, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from application_api.db.models.model import Model
from application_api.schemas.model import ModelCreate
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
    try:
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise
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
    try:
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise
    await db.refresh(db_model)
    return db_model


async def delete_model(db: AsyncSession, model_id: UUID):
    result = await db.execute(
        select(Model).where(Model.model_id == model_id)
    )
    db_model = result.scalar_one_or_none()
    if db_model:
        await db.delete(db_model)
        try:
            await db.commit()
        except SQLAlchemyError:
            await db.rollback()
            raise
    return {"detail": "Model deleted"}


_UPSERT_COMPARE_FIELDS = ("type", "labels", "dimension", "description", "config")


async def upsert_model(db: AsyncSession, model_data: dict) -> str:
    """
    Insert or update a model by name in a single atomic statement.

    Using INSERT ... ON CONFLICT DO UPDATE instead of check-then-insert
    closes the race where two concurrent callers (e.g. two /models/sync
    requests) both see no existing row and both attempt an insert — the
    second used to hit the unique-name violation with nothing catching it,
    surfacing as an unhandled 502.

    Still distinguishes "inserted" / "updated" / "unchanged" (the caller,
    services/monai_sync.py, buckets results by this) in one round trip:
    - the DO UPDATE's WHERE clause only fires when a field actually differs,
      so an unchanged row is left untouched and RETURNING yields no row;
    - `xmax = 0` on the returned row distinguishes a fresh insert from an
      update (Postgres sets a non-zero xmax on the previous row version
      once an UPDATE has occurred; a freshly inserted row has never been
      updated in this transaction).
    """
    stmt = insert(Model).values(**model_data)
    update_columns = {
        key: getattr(stmt.excluded, key)
        for key in _UPSERT_COMPARE_FIELDS
        if key in model_data
    }
    changed_condition = or_(
        *[
            getattr(Model, key).is_distinct_from(getattr(stmt.excluded, key))
            for key in update_columns
        ]
    )
    stmt = (
        stmt.on_conflict_do_update(
            index_elements=["name"],
            set_=update_columns,
            where=changed_condition,
        )
        .returning(literal_column("(xmax = 0)").label("was_insert"))
    )

    try:
        result = await db.execute(stmt)
        row = result.first()
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise

    if row is None:
        return "unchanged"
    return "inserted" if row.was_insert else "updated"