"""Cohort routes"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.api.dependencies import owned_cohort, owned_run, owned_session
from application_api.db.session import get_db
from application_api.services.fencing import fenced_db
from application_api.repositories import cohorts as repo
from application_api.schemas.cohorts import (
    CohortAnalysisRow,
    CohortLabelsWrite,
    CohortMembersSet,
    CohortOut,
    CohortUpsert,
)

router = APIRouter(prefix="/cohorts", tags=["Cohorts"])


@router.get("/session/{session_id}", response_model=list[CohortOut])
async def list_session_cohorts(
    session_id: UUID, db: AsyncSession = Depends(get_db), _: UUID = Depends(owned_session)
):
    """Every cohort a run of this session produced, in plan order."""
    return await repo.list_session_cohorts(db, session_id)


@router.get("/session/{session_id}/population", response_model=CohortOut)
async def read_population_cohort(
    session_id: UUID, db: AsyncSession = Depends(get_db), _: UUID = Depends(owned_session)
):
    """The cohort the session's first population step produced."""
    cohort = await repo.get_population_cohort(db, session_id)
    if cohort is None:
        raise HTTPException(
            status_code=404,
            detail=f"The population step of session {session_id} has not defined a cohort yet",
        )
    return cohort


@router.put("/run/{run_id}/{cohort_key}", response_model=CohortOut)
async def upsert_cohort(
    run_id: UUID,
    cohort_key: str,
    data: CohortUpsert,
    response: Response,
    db: AsyncSession = Depends(fenced_db),
    _: UUID = Depends(owned_run),
):
    """Create a run's draft cohort, or merge criteria/summary into it."""
    cohort, created = await repo.upsert_cohort(db, run_id, cohort_key, data)
    response.status_code = 201 if created else 200
    return cohort


@router.get("/{cohort_id}", response_model=CohortOut)
async def read_cohort(
    cohort_id: UUID, db: AsyncSession = Depends(get_db), _: UUID = Depends(owned_cohort)
):
    return await repo.get_cohort(db, cohort_id)


@router.put("/{cohort_id}/members", response_model=CohortOut)
async def set_cohort_members(
    cohort_id: UUID,
    data: CohortMembersSet,
    db: AsyncSession = Depends(fenced_db),
    _: UUID = Depends(owned_cohort),
):
    """Replace the draft membership. Retained volumes keep their labels."""
    return await repo.set_members(db, cohort_id, data)


@router.patch("/{cohort_id}/labels", response_model=CohortOut)
async def write_cohort_labels(
    cohort_id: UUID,
    data: CohortLabelsWrite,
    db: AsyncSession = Depends(fenced_db),
    _: UUID = Depends(owned_cohort),
):
    """Record labels with their source (LLM extraction, manual override, upload)."""
    return await repo.write_labels(db, cohort_id, data)


@router.post("/{cohort_id}/freeze")
async def freeze_cohort(
    cohort_id: UUID, db: AsyncSession = Depends(get_db), _: UUID = Depends(owned_cohort)
):
    """The doctor's approval. One-way; idempotent once frozen."""
    cohort, event = await repo.freeze_cohort(db, cohort_id)
    rows = await repo.analysis_rows(db, cohort_id)
    return {
        "cohort": CohortOut.model_validate(cohort).model_dump(mode="json"),
        "labeled_count": len(rows),
        "total_count": len(cohort.members),
        "event": (
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "payload": event.payload,
                "created_at": event.created_at.isoformat(),
            }
            if event is not None
            else None
        ),
    }


@router.post("/{cohort_id}/freeze-auto", response_model=CohortOut)
async def auto_freeze_cohort(
    cohort_id: UUID, db: AsyncSession = Depends(fenced_db), _: UUID = Depends(owned_cohort)
):
    """No approval was asked for (D28): the population node freezes its own
    cohort (attempt path, `frozen_by = 'auto'`). Idempotent once frozen."""
    cohort, _event = await repo.auto_freeze_cohort(db, cohort_id)
    return cohort


@router.get("/{cohort_id}/analysis-rows")
async def read_analysis_rows(
    cohort_id: UUID,
    format: str = Query("json", pattern="^(json|csv)$"),
    db: AsyncSession = Depends(get_db),
    _: UUID = Depends(owned_cohort),
):
    """The labelled members in ordinal order — what `targets.csv` used to be."""
    rows = await repo.analysis_rows(db, cohort_id)
    if format == "csv":
        return Response(content=repo.analysis_rows_csv(rows), media_type="text/csv")
    return [CohortAnalysisRow.model_validate(row).model_dump(mode="json") for row in rows]
