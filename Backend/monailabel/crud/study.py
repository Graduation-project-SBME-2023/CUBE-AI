from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from models.study import Study
from schemas.study import StudyCreate, StudyUpdate

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

async def update_study(db: AsyncSession, study_uid: str, updates: StudyUpdate):
    result = await db.execute(select(Study).where(Study.study_uid == study_uid))
    db_study = result.scalar_one_or_none()
    if not db_study:
        return None
    for key, value in updates.dict(exclude_unset=True).items():
        setattr(db_study, key, value)
    await db.commit()
    await db.refresh(db_study)
    return db_study

async def delete_study(db: AsyncSession, study_uid: str):
    result = await db.execute(select(Study).where(Study.study_uid == study_uid))
    db_study = result.scalar_one_or_none()
    if not db_study:
        return None
    await db.delete(db_study)
    await db.commit()
    return db_study

async def get_studies_by_patient(db: AsyncSession, patient_id: int):
    result = await db.execute(select(Study).where(Study.patient_id == patient_id))
    return result.scalars().all()

async def search_studies(db: AsyncSession, modality: str | None = None, keyword: str | None = None):
    query = select(Study)
    if modality:
        query = query.where(Study.modality.ilike(f"%{modality}%"))
    if keyword:
        query = query.where(Study.description.ilike(f"%{keyword}%"))
    result = await db.execute(query)
    return result.scalars().all()

