"""Feature-set routes"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.api.dependencies import owned_feature_set, owned_run, owned_session
from application_api.db.session import get_db
from application_api.services.fencing import fenced_db
from application_api.repositories import feature_sets as repo
from application_api.schemas.feature_sets import (
    ExportPart,
    FeatureSetDetail,
    FeatureSetExport,
    FeatureSetOut,
    FeatureSetWrite,
)

router = APIRouter(prefix="/feature-sets", tags=["Feature sets"])


@router.get("/session/{session_id}", response_model=list[FeatureSetOut])
async def list_session_feature_sets(
    session_id: UUID, db: AsyncSession = Depends(get_db), _: UUID = Depends(owned_session)
):
    """Catalogue only — the matrices are fetched one set at a time."""
    return await repo.list_session_feature_sets(db, session_id)


@router.put("/run/{run_id}/{set_key}", response_model=FeatureSetOut)
async def create_feature_set(
    run_id: UUID,
    set_key: str,
    data: FeatureSetWrite,
    response: Response,
    db: AsyncSession = Depends(fenced_db),
    _: UUID = Depends(owned_run),
):
    """Create a run's `building` set, or replay it (identical manifest → 200)."""
    feature_set, created = await repo.create_feature_set(db, run_id, set_key, data)
    response.status_code = 201 if created else 200
    return feature_set


@router.post("/{feature_set_id}/freeze", response_model=FeatureSetOut)
async def freeze_feature_set(
    feature_set_id: UUID,
    db: AsyncSession = Depends(fenced_db),
    _: UUID = Depends(owned_feature_set),
):
    """Verify counts, digests and cohort keys, then freeze. One-way."""
    feature_set, _event = await repo.freeze_feature_set(db, feature_set_id)
    return feature_set


@router.get("/{feature_set_id}", response_model=FeatureSetDetail | FeatureSetOut)
async def read_feature_set(
    feature_set_id: UUID,
    include_matrix: bool = False,
    db: AsyncSession = Depends(get_db),
    _: UUID = Depends(owned_feature_set),
):
    feature_set = await repo.get_feature_set(db, feature_set_id, include_matrix=include_matrix)
    if include_matrix:
        return FeatureSetDetail.model_validate(feature_set)
    return FeatureSetOut.model_validate(feature_set)


@router.get("/{feature_set_id}/export")
async def export_feature_set(
    feature_set_id: UUID,
    part: ExportPart = "all",
    format: str = Query("json", pattern="^(json|csv)$"),
    include_subject_key: bool = False,
    db: AsyncSession = Depends(get_db),
    _: UUID = Depends(owned_feature_set),
):
    export = await repo.export_rows(db, feature_set_id, part)
    if format == "csv":
        return Response(
            content=repo.export_csv(export, include_subject_key=include_subject_key),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{export["set_key"]}_{part}.csv"'
                )
            },
        )
    return FeatureSetExport.model_validate(export)
