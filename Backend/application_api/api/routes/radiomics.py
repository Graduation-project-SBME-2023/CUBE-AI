"""Radiomics routes"""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.session import get_db
from application_api.repositories import radiomics as crud
from application_api.schemas.radiomics import (
    RadiomicsBatchResult,
    RadiomicsBatchWrite,
    RadiomicsFeatureOut,
)

router = APIRouter(prefix="/radiomics", tags=["Radiomics"])


@router.post("/run/{run_id}/batch", response_model=RadiomicsBatchResult)
async def record_radiomics_rows(
    run_id: UUID,
    data: RadiomicsBatchWrite,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Record extracted rows for a running feature-extraction run."""
    result = await crud.record_features(db, run_id, data)
    response.status_code = 201 if result.created else 200
    return result


@router.get("/session/{session_id}", response_model=List[RadiomicsFeatureOut])
async def list_session_radiomics(
    session_id: UUID,
    run_id: Optional[UUID] = None,
    include_features: bool = False,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    return await crud.list_features(
        db,
        session_id=session_id,
        run_id=run_id,
        include_features=include_features,
        skip=skip,
        limit=limit,
    )


@router.get("/run/{run_id}/export")
async def export_run_radiomics(run_id: UUID, db: AsyncSession = Depends(get_db)):
    """The per-mask feature table one run measured, as a generated CSV."""
    return Response(
        content=await crud.export_run_csv(db, run_id),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="radiomics_features_{run_id}.csv"'
        },
    )


@router.get("/by_label/{label_uid}", response_model=List[RadiomicsFeatureOut])
async def read_radiomics_by_label(
    label_uid: str,
    include_features: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """Every extraction of one SEG — one per segment and configuration."""
    return await crud.list_features(
        db, label_uid=label_uid, include_features=include_features, limit=1000
    )


@router.get("/search/", response_model=List[RadiomicsFeatureOut])
async def search_radiomics_entries(
    feature_key: Optional[str] = Query(None, description="Search by feature key"),
    feature_value: Optional[str] = Query(None, description="Search by feature value"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await crud.search_radiomics(
        db, feature_key=feature_key, feature_value=feature_value, skip=skip, limit=limit
    )


@router.get("/", response_model=List[RadiomicsFeatureOut])
async def read_radiomics_list(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await crud.list_features(db, skip=skip, limit=limit)


@router.get("/{radiomics_id}", response_model=RadiomicsFeatureOut)
async def read_radiomics(radiomics_id: UUID, db: AsyncSession = Depends(get_db)):
    return await crud.get_radiomics(db, radiomics_id)
