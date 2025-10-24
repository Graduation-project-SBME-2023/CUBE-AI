from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from models.study import Study
from schemas.study import StudyCreate

async def get_study(db: AsyncSession, study_uid: str):
    result = await db.execute(
        select(Study).where(Study.study_uid == study_uid)
    )
    return result.scalar_one_or_none()

async def get_studies(db: AsyncSession, skip: int = 0, limit: int = 100):
    result = await db.execute(select(Study).offset(skip).limit(limit))
    return result.scalars().all()

async def create_study(db: AsyncSession, study: StudyCreate):
    db_study = Study(**study.dict())
    db.add(db_study)
    await db.commit()
    await db.refresh(db_study)
    return db_study