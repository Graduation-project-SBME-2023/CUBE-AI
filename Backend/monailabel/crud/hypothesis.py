# app/crud/async_hypothesis.py
from fastapi import HTTPException
from psycopg2 import IntegrityError
from psycopg2 import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from uuid import uuid4, UUID

from models.hypothesis import Hypothesis
from schemas.hypothesis import HypothesisCreate, HypothesisUpdate

async def create_hypothesis(db: AsyncSession, chat_id: UUID, hypothesis_in: HypothesisCreate):
    # check if hypothesis already exists for this chat
    result = await db.execute(select(Hypothesis).where(Hypothesis.chat_id == chat_id))
    existing = result.scalar_one_or_none()

    if existing:
        # return existing or raise an error
        raise HTTPException(
            status_code=409,  # Conflict
            detail=f"Hypothesis already exists for chat_id {chat_id}"
        )

    new_hypothesis = Hypothesis(id=uuid4(), chat_id=chat_id, **hypothesis_in.dict())
    try:
        db.add(new_hypothesis)
        await db.commit()
        await db.refresh(new_hypothesis)
        return new_hypothesis
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Hypothesis already exists for chat_id {chat_id}")
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def get_hypothesis(db: AsyncSession, hypothesis_id: UUID):
    try:
        result = await db.execute(select(Hypothesis).where(Hypothesis.id == hypothesis_id))
        hypothesis = result.scalar_one_or_none()
        if not hypothesis:
            raise HTTPException(status_code=404, detail="Hypothesis not found")
        return hypothesis
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def get_hypothesis_by_chat(db: AsyncSession, chat_id: UUID):
    try:
        result = await db.execute(
            select(Hypothesis).where(Hypothesis.chat_id == chat_id)
        )
        hypothesis = result.scalar_one_or_none()
        if not hypothesis:
            raise HTTPException(status_code=404, detail="Hypothesis not found for this chat")
        return hypothesis
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def get_all_hypotheses(db: AsyncSession, skip: int = 0, limit: int = 100):
    try:
        result = await db.execute(select(Hypothesis).offset(skip).limit(limit))
        return result.scalars().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def update_hypothesis(db: AsyncSession, hypothesis_id: UUID, hypothesis_in: HypothesisUpdate):
    hypothesis = await get_hypothesis(db, hypothesis_id)
    for field, value in hypothesis_in.dict(exclude_unset=True).items():
        setattr(hypothesis, field, value)
    try:
        await db.commit()
        await db.refresh(hypothesis)
        return hypothesis
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def delete_hypothesis(db: AsyncSession, hypothesis_id: UUID):
    hypothesis = await get_hypothesis(db, hypothesis_id)
    try:
        await db.delete(hypothesis)
        await db.commit()
        return {"detail": "Hypothesis deleted"}
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
