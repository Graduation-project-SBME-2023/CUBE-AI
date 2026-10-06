"""Model-result routes"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.services.fencing import fenced_db
from application_api.repositories import model_results as repo
from application_api.schemas.model_results import (
    ModelComparisonOut,
    ModelComparisonWrite,
    ModelResultOut,
)

router = APIRouter(prefix="/model-results", tags=["Model results"])


@router.get("/session/{session_id}", response_model=list[ModelResultOut])
async def list_session_results(
    session_id: UUID, include_documents: bool = False, db: AsyncSession = Depends(get_db)
):
    return await repo.list_session_results(db, session_id, include_documents=include_documents)


@router.get("/session/{session_id}/summary")
async def read_session_summary(session_id: UUID, db: AsyncSession = Depends(get_db)):
    """Per comparison: the selected variant, its input, and whether its
    metrics may be read as a generalisation estimate."""
    return await repo.session_summary(db, session_id)


@router.put("/run/{run_id}/{comparison_key}", response_model=ModelComparisonOut)
async def record_comparison(
    run_id: UUID,
    comparison_key: str,
    data: ModelComparisonWrite,
    response: Response,
    db: AsyncSession = Depends(fenced_db),
):
    """Record every variant of one comparison, or replay it (identical → 200)."""
    comparison, created = await repo.record_comparison(db, run_id, comparison_key, data)
    response.status_code = 201 if created else 200
    return comparison


@router.get("/run/{run_id}/{comparison_key}", response_model=ModelComparisonOut)
async def read_comparison(
    run_id: UUID, comparison_key: str, db: AsyncSession = Depends(get_db)
):
    comparison = await repo.get_comparison(db, run_id, comparison_key)
    if comparison is None:
        raise HTTPException(
            status_code=404,
            detail=f"Comparison {comparison_key!r} of run {run_id} is not recorded",
        )
    return comparison


@router.get("/{model_result_id}", response_model=ModelResultOut)
async def read_model_result(model_result_id: UUID, db: AsyncSession = Depends(get_db)):
    return await repo.get_result(db, model_result_id)
