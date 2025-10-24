from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from models.radiomics import Radiomics
from schemas.radiomics import RadiomicsCreate

async def get_radiomics(db: AsyncSession, radiomics_id: str):
    result = await db.execute(
        select(Radiomics).where(Radiomics.id == radiomics_id)
    )
    return result.scalar_one_or_none()

async def get_radiomics_list(db: AsyncSession, skip: int = 0, limit: int = 100):
    result = await db.execute(select(Radiomics).offset(skip).limit(limit))
    return result.scalars().all()

async def create_radiomics(db: AsyncSession, radiomics: RadiomicsCreate):
    db_radiomics = Radiomics(**radiomics.dict())
    db.add(db_radiomics)
    await db.commit()
    await db.refresh(db_radiomics)
    return db_radiomics
