"""Radiomics repository"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import String, cast, select, tuple_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.cohorts import CohortMember
from application_api.db.models.radiomics import Radiomics
from application_api.db.models.segmentation_batches import SegmentationPair, SegmentationTarget
from application_api.repositories import node_runs as runs_repo
from application_api.repositories.users import owned_session_ids
from application_api.schemas import radiomics as schemas
from application_api.services.publication import lock_session

logger = logging.getLogger(__name__)

FEATURE_EXTRACTION_AGENT_TYPE = "feature_extraction_agent"

# The immutable measured payload a replay must reproduce exactly.
_COMPARED_FIELDS = (
    "source_volume_uid",
    "segmentation_pair_id",
    "features",
    "extractor_name",
    "extractor_version",
    "configuration",
    "image_digest",
    "segmentation_digest",
    "schema_version",
)

_CATALOGUE_COLUMNS = (
    Radiomics.radiomics_feature_id,
    Radiomics.session_id,
    Radiomics.label_series_instance_uid,
    Radiomics.source_volume_uid,
    Radiomics.segment_number,
    Radiomics.segmentation_pair_id,
    Radiomics.created_by_run_id,
    Radiomics.extractor_name,
    Radiomics.extractor_version,
    Radiomics.config_digest,
    Radiomics.image_digest,
    Radiomics.segmentation_digest,
    Radiomics.source_digest,
    Radiomics.schema_version,
    Radiomics.created_at,
)


async def require_producer_run(db: AsyncSession, run_id: UUID, agent_type: str, what: str):
    """A domain is written only by the agent that owns it, while it runs."""
    run = await runs_repo.get_run(db, run_id)
    if run.node.target_name != agent_type:
        raise HTTPException(
            status_code=403,
            detail=(
                f"{what} are owned by {agent_type}; run {run_id} executes "
                f"{run.node.target_name} and cannot write them (D17)."
            ),
        )
    if run.status != "running":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Run {run_id} is {run.status!r}; results are recorded only "
                "while the run is running."
            ),
        )
    return run


def _key(row: Any) -> tuple:
    return (
        row.label_series_instance_uid,
        int(row.segment_number),
        row.config_digest,
        row.source_digest,
    )


def _payload(row: Any) -> dict[str, Any]:
    payload = {field: getattr(row, field) for field in _COMPARED_FIELDS}
    if payload["segmentation_pair_id"] is not None:
        payload["segmentation_pair_id"] = str(payload["segmentation_pair_id"])
    return payload


async def record_features(
    db: AsyncSession, run_id: UUID, data: schemas.RadiomicsBatchWrite
) -> schemas.RadiomicsBatchResult:
    run = await require_producer_run(
        db, run_id, FEATURE_EXTRACTION_AGENT_TYPE, "Radiomics features"
    )
    session_id = run.session_id

    # Collapse exact duplicates inside the batch; a key repeated with different
    # content is the writer contradicting itself.
    incoming: dict[tuple, schemas.RadiomicsFeatureWrite] = {}
    for row in data.rows:
        key = _key(row)
        if key in incoming and _payload(incoming[key]) != _payload(row):
            raise HTTPException(
                status_code=422,
                detail=f"The batch names {key[:2]} twice with different content.",
            )
        incoming[key] = row

    await lock_session(db, session_id)
    existing_rows = (
        await db.execute(
            select(Radiomics).where(
                Radiomics.session_id == session_id,
                tuple_(
                    Radiomics.label_series_instance_uid,
                    Radiomics.segment_number,
                    Radiomics.config_digest,
                    Radiomics.source_digest,
                ).in_(list(incoming)),
            )
        )
    ).scalars().all()
    existing = {_key(row): row for row in existing_rows}

    conflicts = [
        {
            "label_series_instance_uid": key[0],
            "segment_number": key[1],
            "source_digest": key[3],
            "differs_in": [
                field
                for field in _COMPARED_FIELDS
                if _payload(existing[key])[field] != _payload(row)[field]
            ],
        }
        for key, row in incoming.items()
        if key in existing and _payload(existing[key]) != _payload(row)
    ]
    if conflicts:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "reconciliation_required",
                "message": (
                    f"{len(conflicts)} radiomics row(s) already exist under the same "
                    "content-addressed key with different measured content. Identical "
                    "content is reused; different content is never silently accepted."
                ),
                "conflicts": conflicts[:20],
            },
        )

    refs: list[schemas.RadiomicsFeatureRef] = []
    created_rows: list[Radiomics] = []
    for key, row in incoming.items():
        if key in existing:
            refs.append(
                schemas.RadiomicsFeatureRef(
                    radiomics_feature_id=existing[key].radiomics_feature_id,
                    label_series_instance_uid=key[0],
                    segment_number=key[1],
                    source_digest=key[3],
                    created=False,
                )
            )
            continue
        record = Radiomics(
            session_id=session_id,
            created_by_run_id=run_id,
            **row.model_dump(),
        )
        db.add(record)
        created_rows.append(record)

    try:
        await db.flush()
        for record in created_rows:
            refs.append(
                schemas.RadiomicsFeatureRef(
                    radiomics_feature_id=record.radiomics_feature_id,
                    label_series_instance_uid=record.label_series_instance_uid,
                    segment_number=record.segment_number,
                    source_digest=record.source_digest,
                    created=True,
                )
            )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Radiomics rows rejected: {exc.orig}",
        ) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc

    return schemas.RadiomicsBatchResult(
        created=len(created_rows), reused=len(incoming) - len(created_rows), rows=refs
    )


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------
def _catalogue_query(include_features: bool):
    columns = [
        *_CATALOGUE_COLUMNS,
        SegmentationTarget.target_key,
        CohortMember.subject_key,
    ]
    if include_features:
        columns += [Radiomics.features, Radiomics.configuration]
    return (
        select(*columns)
        .outerjoin(SegmentationPair, SegmentationPair.pair_id == Radiomics.segmentation_pair_id)
        .outerjoin(SegmentationTarget, SegmentationTarget.target_id == SegmentationPair.target_id)
        .outerjoin(CohortMember, CohortMember.member_id == SegmentationPair.cohort_member_id)
    )


def _row_out(row) -> dict[str, Any]:
    out = dict(row._mapping)
    out["provenance_complete"] = all(
        out.get(field) is not None
        for field in (
            "session_id",
            "source_volume_uid",
            "segment_number",
            "extractor_name",
            "config_digest",
            "source_digest",
        )
    )
    return out


async def list_features(
    db: AsyncSession,
    *,
    session_id: UUID | None = None,
    run_id: UUID | None = None,
    label_uid: str | None = None,
    include_features: bool = False,
    skip: int = 0,
    limit: int = 100,
    owner: UUID | None = None,
) -> list[dict[str, Any]]:
    query = _catalogue_query(include_features)
    if owner is not None:
        query = query.where(Radiomics.session_id.in_(owned_session_ids(owner)))
    if session_id is not None:
        query = query.where(Radiomics.session_id == session_id)
    if run_id is not None:
        query = query.where(Radiomics.created_by_run_id == run_id)
    if label_uid is not None:
        query = query.where(Radiomics.label_series_instance_uid == label_uid)
    result = await db.execute(
        query.order_by(Radiomics.created_at, Radiomics.radiomics_feature_id).offset(skip).limit(limit)
    )
    return [_row_out(row) for row in result]


async def get_radiomics(db: AsyncSession, radiomics_id: UUID) -> dict[str, Any]:
    result = await db.execute(_catalogue_query(True).where(Radiomics.radiomics_feature_id == radiomics_id))
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Radiomics {radiomics_id} not found")
    return _row_out(row)


async def search_radiomics(
    db: AsyncSession,
    feature_key: str | None = None,
    feature_value: str | None = None,
    skip: int = 0,
    limit: int = 100,
    owner: UUID | None = None,
) -> list[dict[str, Any]]:
    """Rows whose features contain a key (and, optionally, a value)."""
    query = _catalogue_query(True)
    if owner is not None:
        query = query.where(Radiomics.session_id.in_(owned_session_ids(owner)))
    if feature_key:
        query = query.where(Radiomics.features.op("?")(feature_key))
        if feature_value:
            query = query.where(
                cast(Radiomics.features[feature_key], String).ilike(f"%{feature_value}%")
            )
    result = await db.execute(
        query.order_by(Radiomics.created_at, Radiomics.radiomics_feature_id).offset(skip).limit(limit)
    )
    return [_row_out(row) for row in result]


async def export_run_csv(db: AsyncSession, run_id: UUID) -> str:
    """Every row one run measured, one line per (SEG, segment), as CSV."""
    rows = await list_features(
        db, run_id=run_id, include_features=True, limit=1_000_000
    )
    names = sorted({name for row in rows for name in (row.get("features") or {})})
    identity = [
        "radiomics_feature_id",
        "subject_key",
        "source_volume_uid",
        "label_series_instance_uid",
        "segment_number",
        "target_key",
        "source_digest",
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([*identity, *names])
    for row in rows:
        features = row.get("features") or {}
        writer.writerow(
            [*(row.get(column) for column in identity), *(features.get(name) for name in names)]
        )
    return buffer.getvalue()
