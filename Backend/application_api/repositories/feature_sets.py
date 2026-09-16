"""Feature-set repository"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.agent_runs import AgentRun
from application_api.db.models.feature_sets import FeatureSet
from application_api.db.models.radiomics import Radiomics
from application_api.db.models.steps import PlanStep
from application_api.repositories import agent_runs as runs_repo
from application_api.repositories import cohorts as cohorts_repo
from application_api.repositories.radiomics import (
    FEATURE_EXTRACTION_AGENT_TYPE,
    require_producer_run,
)
from application_api.schemas import feature_sets as schemas
from application_api.services.publication import lock_session, publication

logger = logging.getLogger(__name__)

DIGEST_SCHEMA_VERSION = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def rows_source_digest(source_digests: list[str]) -> str:
    """Verbatim twin of `mcp/tools/feature_extraction/digests.rows_source_digest`
    (the API cannot import MCP code; a contract test pins them together)."""
    return hashlib.sha256(
        _canonical(
            {
                "schema_version": DIGEST_SCHEMA_VERSION,
                "radiomics_source_digests": sorted(str(item) for item in source_digests),
            }
        )
    ).hexdigest()


def manifest_digest(
    *,
    cohort_id: UUID,
    set_key: str,
    feature_names: list[str],
    feature_matrix: dict[str, list[Any]],
    source_feature_ids: list[str] | None,
    selection: dict[str, Any],
    config_digest: str,
    source_digest: str,
) -> str:
    """Over everything a frozen set promises. Matrix keys are sorted by the
    canonical encoding, so JSON key order cannot change the digest."""
    return hashlib.sha256(
        _canonical(
            {
                "schema_version": DIGEST_SCHEMA_VERSION,
                "cohort_id": str(cohort_id),
                "set_key": set_key,
                "feature_names": list(feature_names),
                "feature_matrix": feature_matrix,
                "source_feature_ids": (
                    sorted(str(item) for item in source_feature_ids)
                    if source_feature_ids is not None
                    else None
                ),
                "selection": selection,
                "config_digest": config_digest,
                "source_digest": source_digest,
            }
        )
    ).hexdigest()


def _stored_manifest_digest(feature_set: FeatureSet) -> str:
    return manifest_digest(
        cohort_id=feature_set.cohort_id,
        set_key=feature_set.set_key,
        feature_names=feature_set.feature_names,
        feature_matrix=feature_set.feature_matrix,
        source_feature_ids=feature_set.source_feature_ids,
        selection=feature_set.selection,
        config_digest=feature_set.config_digest,
        source_digest=feature_set.source_digest,
    )


async def _abort(db: AsyncSession, exc: HTTPException) -> HTTPException:
    await db.rollback()
    return exc


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------
_CATALOGUE_COLUMNS = (
    FeatureSet.feature_set_id,
    FeatureSet.created_by_agent_run_id,
    FeatureSet.cohort_id,
    FeatureSet.set_key,
    FeatureSet.schema_version,
    FeatureSet.feature_names,
    FeatureSet.selection,
    FeatureSet.extraction_config,
    FeatureSet.analysis,
    FeatureSet.quality_summary,
    FeatureSet.config_digest,
    FeatureSet.source_digest,
    FeatureSet.manifest_digest,
    FeatureSet.status,
    FeatureSet.frozen_at,
    FeatureSet.created_at,
    AgentRun.session_id,
    func.jsonb_array_length(FeatureSet.feature_names).label("feature_count"),
    # NULL (not zero) when the set was not selected from radiomics rows.
    func.jsonb_array_length(FeatureSet.source_feature_ids).label("source_feature_count"),
)


def _catalogue_out(row) -> dict[str, Any]:
    out = dict(row._mapping)
    # The subject count is verified against the matrix at freeze, so the
    # catalogue can read it without detoasting the matrix itself.
    out["subject_count"] = int((out.get("quality_summary") or {}).get("subjects") or 0)
    return out


def _catalogue_query():
    return select(*_CATALOGUE_COLUMNS).join(
        AgentRun, AgentRun.agent_run_id == FeatureSet.created_by_agent_run_id
    )


async def _entity(db: AsyncSession, feature_set_id: UUID) -> FeatureSet:
    feature_set = (
        await db.execute(select(FeatureSet).where(FeatureSet.feature_set_id == feature_set_id))
    ).scalar_one_or_none()
    if feature_set is None:
        raise HTTPException(status_code=404, detail=f"Feature set {feature_set_id} not found")
    return feature_set


async def get_feature_set(
    db: AsyncSession, feature_set_id: UUID, *, include_matrix: bool = False
) -> dict[str, Any]:
    row = (
        await db.execute(_catalogue_query().where(FeatureSet.feature_set_id == feature_set_id))
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Feature set {feature_set_id} not found")
    out = _catalogue_out(row)
    if include_matrix:
        matrix, sources = (
            await db.execute(
                select(FeatureSet.feature_matrix, FeatureSet.source_feature_ids).where(
                    FeatureSet.feature_set_id == feature_set_id
                )
            )
        ).one()
        out["feature_matrix"] = matrix
        out["source_feature_ids"] = sources
        out["subject_count"] = len(matrix)
    return out


async def list_session_feature_sets(db: AsyncSession, session_id: UUID) -> list[dict[str, Any]]:
    result = await db.execute(
        _catalogue_query()
        .join(PlanStep, PlanStep.step_id == AgentRun.step_id)
        .where(AgentRun.session_id == session_id)
        .order_by(PlanStep.step_order, FeatureSet.created_at, FeatureSet.set_key)
    )
    return [_catalogue_out(row) for row in result]


async def find_feature_set(db: AsyncSession, agent_run_id: UUID, set_key: str) -> FeatureSet | None:
    return (
        await db.execute(
            select(FeatureSet).where(
                FeatureSet.created_by_agent_run_id == agent_run_id,
                FeatureSet.set_key == set_key,
            )
        )
    ).scalar_one_or_none()


# ----------------------------------------------------------------------
# Create (insert-or-read)
# ----------------------------------------------------------------------
async def create_feature_set(
    db: AsyncSession, agent_run_id: UUID, set_key: str, data: schemas.FeatureSetWrite
) -> tuple[dict[str, Any], bool]:
    if not set_key.strip():
        raise HTTPException(status_code=422, detail="set_key must not be blank")
    run = await require_producer_run(db, agent_run_id, FEATURE_EXTRACTION_AGENT_TYPE, "Feature sets")
    await lock_session(db, run.session_id)

    cohort = await cohorts_repo.get_cohort(db, data.cohort_id)
    cohort_run = await runs_repo.get_run(db, cohort.created_by_agent_run_id)
    if cohort_run.session_id != run.session_id:
        raise await _abort(
            db,
            HTTPException(
                status_code=409,
                detail=(
                    f"Cohort {data.cohort_id} belongs to session {cohort_run.session_id}; a "
                    f"feature set of session {run.session_id} cannot use it (R1)."
                ),
            ),
        )
    label_field = str(cohort.label_definition.get("field", "has_target_disease"))
    if label_field in data.feature_names:
        raise await _abort(
            db,
            HTTPException(
                status_code=422,
                detail=(
                    f"{label_field!r} is the cohort's label; it is joined at read time and "
                    "never stored as a feature."
                ),
            ),
        )
    subject_keys = {member.subject_key for member in cohort.members}
    strangers = [key for key in data.feature_matrix if key not in subject_keys]
    if strangers:
        raise await _abort(
            db,
            HTTPException(
                status_code=422,
                detail=(
                    f"{len(strangers)} feature_matrix key(s) are not subject keys of cohort "
                    f"{data.cohort_id}: {strangers[:10]}"
                ),
            ),
        )

    source_ids = [str(item) for item in data.source_feature_ids] if data.source_feature_ids else None
    source_digest = data.source_digest
    if data.source_feature_ids is not None:
        sources = (
            await db.execute(
                select(Radiomics.id, Radiomics.session_id, Radiomics.source_digest).where(
                    Radiomics.id.in_(data.source_feature_ids)
                )
            )
        ).all()
        found = {str(row.id): row for row in sources}
        missing = [item for item in source_ids if item not in found]
        foreign = [item for item, row in found.items() if row.session_id != run.session_id]
        legacy = [item for item, row in found.items() if row.source_digest is None]
        problem = None
        if missing:
            problem = (422, f"{len(missing)} source radiomics row(s) do not exist: {missing[:5]}")
        elif foreign:
            problem = (409, f"{len(foreign)} source row(s) belong to another session (R1).")
        elif legacy:
            problem = (
                409,
                f"{len(legacy)} source row(s) are legacy radiomics rows with NULL provenance; "
                "they can never enter a feature set (R9).",
            )
        if problem:
            raise await _abort(db, HTTPException(status_code=problem[0], detail=problem[1]))
        computed = rows_source_digest([found[item].source_digest for item in source_ids])
        if source_digest is not None and source_digest != computed:
            raise await _abort(
                db,
                HTTPException(
                    status_code=409,
                    detail=(
                        "source_digest does not match the digests of the named radiomics "
                        "rows; a feature set's provenance is derived, not asserted."
                    ),
                ),
            )
        source_digest = computed

    matrix = {key: list(values) for key, values in data.feature_matrix.items()}
    digest = manifest_digest(
        cohort_id=data.cohort_id,
        set_key=set_key,
        feature_names=data.feature_names,
        feature_matrix=matrix,
        source_feature_ids=source_ids,
        selection=data.selection,
        config_digest=data.config_digest,
        source_digest=source_digest,
    )

    existing = await find_feature_set(db, agent_run_id, set_key)
    if existing is not None:
        if existing.manifest_digest != digest:
            raise await _abort(
                db,
                HTTPException(
                    status_code=409,
                    detail={
                        "code": "reconciliation_required",
                        "message": (
                            f"Feature set {set_key!r} of this run already exists with a "
                            "different manifest. A replay must reproduce it exactly; a "
                            "different selection is never silently accepted."
                        ),
                        "feature_set_id": str(existing.feature_set_id),
                    },
                ),
            )
        await db.commit()
        return await get_feature_set(db, existing.feature_set_id), False

    feature_set = FeatureSet(
        created_by_agent_run_id=agent_run_id,
        cohort_id=data.cohort_id,
        set_key=set_key,
        schema_version=data.schema_version,
        feature_names=list(data.feature_names),
        feature_matrix=matrix,
        source_feature_ids=source_ids,
        selection=data.selection,
        extraction_config=data.extraction_config,
        analysis=data.analysis,
        quality_summary=data.quality_summary,
        config_digest=data.config_digest,
        source_digest=source_digest,
        manifest_digest=digest,
        status="building",
    )
    db.add(feature_set)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Feature set rejected: {exc.orig}") from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    return await get_feature_set(db, feature_set.feature_set_id), True


# ----------------------------------------------------------------------
# Freeze
# ----------------------------------------------------------------------
async def freeze_feature_set(db: AsyncSession, feature_set_id: UUID) -> tuple[dict[str, Any], Any]:
    """Verify and freeze. Idempotent once frozen; publishes nothing then."""
    feature_set = await _entity(db, feature_set_id)
    run = await runs_repo.get_run(db, feature_set.created_by_agent_run_id)
    session_id = run.session_id
    await lock_session(db, session_id)
    await db.execute(
        text("SELECT feature_set_id FROM feature_sets WHERE feature_set_id = :fid FOR UPDATE"),
        {"fid": str(feature_set_id)},
    )
    await db.refresh(feature_set)
    if feature_set.status == "frozen":
        await db.commit()
        return await get_feature_set(db, feature_set_id), None

    problems = []
    if _stored_manifest_digest(feature_set) != feature_set.manifest_digest:
        problems.append("the stored content no longer matches its manifest_digest")
    summary = feature_set.quality_summary or {}
    if summary.get("subjects") != len(feature_set.feature_matrix):
        problems.append(
            f"quality_summary.subjects={summary.get('subjects')!r} but the matrix has "
            f"{len(feature_set.feature_matrix)} subject(s)"
        )
    if summary.get("features") != len(feature_set.feature_names):
        problems.append(
            f"quality_summary.features={summary.get('features')!r} but there are "
            f"{len(feature_set.feature_names)} feature name(s)"
        )
    if problems:
        raise await _abort(
            db,
            HTTPException(
                status_code=409,
                detail=f"Feature set {feature_set_id} cannot be frozen: {'; '.join(problems)}.",
            ),
        )

    event = None
    try:
        async with publication(db, session_id) as publish:
            feature_set.status = "frozen"
            feature_set.frozen_at = _now()
            await db.flush()
            event = publish(
                "feature_set_frozen",
                {
                    "schema_version": 1,
                    "feature_set_id": str(feature_set_id),
                    "set_key": feature_set.set_key,
                    "cohort_id": str(feature_set.cohort_id),
                    "created_by_agent_run_id": str(feature_set.created_by_agent_run_id),
                    "feature_count": len(feature_set.feature_names),
                    "label_supervised_on_full_data": bool(
                        feature_set.selection.get("label_supervised_on_full_data")
                    ),
                },
            )
    except HTTPException as exc:
        # The trigger's own checks
        # surface here as a database error; the set stays `building`.
        raise HTTPException(
            status_code=409, detail=f"Feature set {feature_set_id} cannot be frozen: {exc.detail}"
        ) from exc
    return await get_feature_set(db, feature_set_id), event


# ----------------------------------------------------------------------
# Export — the modeling handoff
# ----------------------------------------------------------------------
async def export_rows(
    db: AsyncSession, feature_set_id: UUID, part: str = "all"
) -> dict[str, Any]:
    """Rows in cohort order with the cohort label joined at read time."""
    feature_set = await _entity(db, feature_set_id)
    selection = feature_set.selection or {}
    holdout = {str(group) for group in selection.get("holdout_groups") or []}
    if part != "all" and holdout and selection.get("group_column") != "subject_key":
        raise HTTPException(
            status_code=409,
            detail=(
                "This set's holdout was grouped by "
                f"{selection.get('group_column')!r}, not subject_key; its split cannot be "
                "reconstructed from the matrix. Export part='all'."
            ),
        )

    analysis = await cohorts_repo.analysis_rows(db, feature_set.cohort_id)
    targets: dict[str, Any] = {}
    first_ordinal: dict[str, int] = {}
    for row in analysis:
        key = row["subject_key"]
        if key in targets and targets[key] != row["target"]:
            raise HTTPException(
                status_code=409,
                detail=f"Subject {key!r} has members with different labels in the cohort.",
            )
        targets[key] = row["target"]
        first_ordinal[key] = min(first_ordinal.get(key, row["ordinal"]), row["ordinal"])

    unlabelled = [key for key in feature_set.feature_matrix if key not in targets]
    if unlabelled:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{len(unlabelled)} subject(s) in the matrix have no valid cohort label: "
                f"{unlabelled[:5]}"
            ),
        )

    rows = []
    for subject in sorted(feature_set.feature_matrix, key=lambda key: (first_ordinal[key], key)):
        split = "holdout" if subject in holdout else "train"
        if part != "all" and split != part:
            continue
        rows.append(
            {
                "subject_key": subject,
                "ordinal": first_ordinal[subject],
                "split": split,
                "target": targets[subject],
                "values": feature_set.feature_matrix[subject],
            }
        )
    return {
        "feature_set_id": feature_set.feature_set_id,
        "cohort_id": feature_set.cohort_id,
        "set_key": feature_set.set_key,
        "part": part,
        "feature_names": feature_set.feature_names,
        "label_supervised_on_full_data": bool(selection.get("label_supervised_on_full_data")),
        "rows": rows,
    }


def export_csv(export: dict[str, Any], *, include_subject_key: bool = False) -> str:
    """The modeling CSV: features, then `target`. Generated, never stored."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    header = [*export["feature_names"], "target"]
    if include_subject_key:
        header = ["subject_key", *header]
    writer.writerow(header)
    for row in export["rows"]:
        values = ["" if value is None else value for value in row["values"]]
        line = [*values, row["target"]]
        if include_subject_key:
            line = [row["subject_key"], *line]
        writer.writerow(line)
    return buffer.getvalue()
