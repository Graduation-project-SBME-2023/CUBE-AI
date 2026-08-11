# app/routers/async_hypothesis.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from typing import List

from core.database import get_db
from crud.hypothesis import (
    create_hypothesis,
    get_hypothesis,
    get_all_hypotheses,
    update_hypothesis,
    delete_hypothesis,
    get_hypothesis_by_chat
)
from schemas.hypothesis import HypothesisCreate, HypothesisUpdate, HypothesisOut
from crud.plan import get_plans_for_hypothesis  # optional nested

router = APIRouter(prefix="/hypotheses", tags=["Hypotheses"])

@router.post("/", response_model=HypothesisOut)
async def create_hypothesis_endpoint(chat_id: UUID, hypothesis_in: HypothesisCreate, db: AsyncSession = Depends(get_db)):
    return await create_hypothesis(db, chat_id, hypothesis_in)

@router.get("/", response_model=List[HypothesisOut])
async def list_hypotheses(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await get_all_hypotheses(db, skip, limit)

@router.get("/{hypothesis_id}", response_model=HypothesisOut)
async def read_hypothesis(hypothesis_id: UUID, db: AsyncSession = Depends(get_db)):
    return await get_hypothesis(db, hypothesis_id)

@router.get("/by-chat/{chat_id}", response_model=HypothesisOut)
async def get_hypothesis_for_chat(chat_id: UUID, db: AsyncSession = Depends(get_db)):
    return await get_hypothesis_by_chat(db, chat_id)

@router.patch("/{hypothesis_id}", response_model=HypothesisOut)
async def edit_hypothesis(hypothesis_id: UUID, hypothesis_in: HypothesisUpdate, db: AsyncSession = Depends(get_db)):
    return await update_hypothesis(db, hypothesis_id, hypothesis_in)

@router.delete("/{hypothesis_id}")
async def remove_hypothesis(hypothesis_id: UUID, db: AsyncSession = Depends(get_db)):
    return await delete_hypothesis(db, hypothesis_id)
