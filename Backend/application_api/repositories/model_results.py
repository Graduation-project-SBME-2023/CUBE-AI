"""Model-result repository """

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.node_runs import NodeRun
from application_api.db.models.stored_files import StoredFile
from application_api.db.models.feature_sets import FeatureSet
from application_api.db.models.model_results import ModelResult
from application_api.db.models.graph_nodes import GraphNode
from application_api.repositories import node_runs as runs_repo
from application_api.repositories import cohorts as cohorts_repo
from application_api.repositories.radiomics import require_producer_run
from application_api.schemas import model_results as schemas
from application_api.services.publication import lock_session, publication

logger = logging.getLogger(__name__)

MODELING_AGENT_TYPE = "modeling_agent"

# Shared by every variant of one comparison.
_SHARED_FIELDS = (
    "cohort_id",
    "feature_set_id",
    "input_file_id",
    "dataset_digest",
    "preprocessing",
    "feature_order",
    "split_definition",
    "seeds",
    "environment",
)
_VARIANT_FIELDS = (
    "algorithm",
    "parameters",
    "metrics",
    "predictions_file_id",
    "model_file_name",
    "model_file_digest",
    "selected",
    "status",
)


def _normalized(value: Any) -> Any:
    return str(value) if isinstance(value, UUID) else value


def _row_payload(row: Any) -> dict[str, Any]:
    return {
        field: _normalized(getattr(row, field)) for field in (*_SHARED_FIELDS, *_VARIANT_FIELDS)
    }


def _write_payload(data: schemas.ModelComparisonWrite, variant: schemas.ModelVariantWrite):
    shared = {field: _normalized(getattr(data, field)) for field in _SHARED_FIELDS}
    own = {field: _normalized(getattr(variant, field)) for field in _VARIANT_FIELDS}
    return {**shared, **own}


async def _abort(db: AsyncSession, exc: HTTPException) -> HTTPException:
    await db.rollback()
    return exc


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------
def _columns(include_documents: bool):
    base = [
        ModelResult.model_result_id,
        ModelResult.created_by_run_id,
        NodeRun.session_id,
        ModelResult.cohort_id,
        ModelResult.feature_set_id,
        ModelResult.comparison_key,
        ModelResult.variant_key,
        ModelResult.algorithm,
        ModelResult.metrics,
        ModelResult.dataset_digest,
        ModelResult.predictions_file_id,
        ModelResult.input_file_id,
        ModelResult.selected,
        ModelResult.model_file_name,
        ModelResult.model_file_digest,
        ModelResult.status,
        ModelResult.created_at,
    ]
    if include_documents:
        base += [
            ModelResult.parameters,
            ModelResult.preprocessing,
            ModelResult.feature_order,
            ModelResult.split_definition,
            ModelResult.seeds,
            ModelResult.environment,
        ]
    return base


def _query(include_documents: bool = True):
    return select(*_columns(include_documents)).join(
        NodeRun, NodeRun.run_id == ModelResult.created_by_run_id
    )


async def get_result(db: AsyncSession, model_result_id: UUID) -> dict[str, Any]:
    row = (
        await db.execute(_query().where(ModelResult.model_result_id == model_result_id))
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Model result {model_result_id} not found")
    return dict(row._mapping)


def _comparison_out(rows: list[dict[str, Any]]) -> dict[str, Any]:
    first = rows[0]
    selected = next((row["variant_key"] for row in rows if row["selected"]), None)
    return {
        "comparison_key": first["comparison_key"],
        "created_by_run_id": first["created_by_run_id"],
        "session_id": first["session_id"],
        "cohort_id": first["cohort_id"],
        "feature_set_id": first["feature_set_id"],
        "input_file_id": first["input_file_id"],
        "dataset_digest": first["dataset_digest"],
        "selected_variant_key": selected,
        "variants": rows,
    }


async def get_comparison(
    db: AsyncSession, run_id: UUID, comparison_key: str
) -> dict[str, Any] | None:
    result = await db.execute(
        _query()
        .where(
            ModelResult.created_by_run_id == run_id,
            ModelResult.comparison_key == comparison_key,
        )
        .order_by(ModelResult.variant_key)
    )
    rows = [dict(row._mapping) for row in result]
    return _comparison_out(rows) if rows else None


async def list_session_results(
    db: AsyncSession, session_id: UUID, *, include_documents: bool = False
) -> list[dict[str, Any]]:
    result = await db.execute(
        _query(include_documents)
        .join(GraphNode, GraphNode.node_id == NodeRun.node_id)
        .where(NodeRun.session_id == session_id)
        .order_by(GraphNode.position, ModelResult.comparison_key, ModelResult.variant_key)
    )
    return [dict(row._mapping) for row in result]


async def session_summary(db: AsyncSession, session_id: UUID) -> list[dict[str, Any]]:
    """What the reporter reads instead of `model_summary.csv`."""
    rows = await list_session_results(db, session_id, include_documents=True)
    sets: dict[str, dict[str, Any]] = {}
    set_ids = {row["feature_set_id"] for row in rows if row["feature_set_id"]}
    if set_ids:
        for fs in (
            await db.execute(
                select(
                    FeatureSet.feature_set_id,
                    FeatureSet.set_key,
                    FeatureSet.feature_names,
                    FeatureSet.selection,
                ).where(FeatureSet.feature_set_id.in_(set_ids))
            )
        ).all():
            sets[str(fs.feature_set_id)] = {
                "feature_set_id": fs.feature_set_id,
                "set_key": fs.set_key,
                "feature_count": len(fs.feature_names or []),
                "feature_names": list(fs.feature_names or [])[:50],
                "label_supervised_on_full_data": bool(
                    (fs.selection or {}).get("label_supervised_on_full_data")
                ),
            }
    comparisons: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        comparisons.setdefault((row["created_by_run_id"], row["comparison_key"]), []).append(row)
    summary = []
    for (_run, key), variants in comparisons.items():
        first = variants[0]
        selected = next((row for row in variants if row["selected"]), None)
        feature_set = sets.get(str(first["feature_set_id"])) if first["feature_set_id"] else None
        summary.append(
            {
                "comparison_key": key,
                "created_by_run_id": first["created_by_run_id"],
                "cohort_id": first["cohort_id"],
                "feature_set": feature_set,
                "input_file_id": first["input_file_id"],
                "dataset_digest": first["dataset_digest"],
                "split_definition": first["split_definition"],
                "seeds": first["seeds"],
                "selected": (
                    {
                        "variant_key": selected["variant_key"],
                        "algorithm": selected["algorithm"],
                        "metrics": selected["metrics"],
                        "predictions_file_id": selected["predictions_file_id"],
                        "model_file_name": selected["model_file_name"],
                    }
                    if selected
                    else None
                ),
                "variants": [
                    {
                        "variant_key": row["variant_key"],
                        "algorithm": row["algorithm"],
                        "status": row["status"],
                        "selected": row["selected"],
                        "metrics": row["metrics"],
                    }
                    for row in variants
                ],
            }
        )
    return summary


# ----------------------------------------------------------------------
# Record a comparison (insert-or-read)
# ----------------------------------------------------------------------
async def _check_inputs(
    db: AsyncSession, run: NodeRun, data: schemas.ModelComparisonWrite
) -> None:
    cohort = await cohorts_repo.get_cohort(db, data.cohort_id)
    cohort_run = await runs_repo.get_run(db, cohort.created_by_run_id)
    if cohort_run.session_id != run.session_id:
        raise await _abort(
            db,
            HTTPException(
                status_code=409,
                detail=(
                    f"Cohort {data.cohort_id} belongs to session {cohort_run.session_id}; a "
                    f"model result of session {run.session_id} cannot use it (R1)."
                ),
            ),
        )

    if data.feature_set_id is not None:
        feature_set = (
            await db.execute(
                text(
                    "SELECT (CASE WHEN frozen_at IS NOT NULL THEN 'frozen' ELSE 'building' END) "
                    "AS status, cohort_id FROM feature_sets "
                    "WHERE feature_set_id = :fid FOR SHARE"
                ),
                {"fid": str(data.feature_set_id)},
            )
        ).first()
        if feature_set is None:
            raise await _abort(
                db,
                HTTPException(status_code=422, detail=f"Feature set {data.feature_set_id} not found"),
            )
        if feature_set.status != "frozen":
            raise await _abort(
                db,
                HTTPException(
                    status_code=409,
                    detail=f"Feature set {data.feature_set_id} is not frozen; modeling trains only on frozen sets.",
                ),
            )
        if feature_set.cohort_id != data.cohort_id:
            raise await _abort(
                db,
                HTTPException(
                    status_code=409,
                    detail=(
                        f"Feature set {data.feature_set_id} belongs to cohort "
                        f"{feature_set.cohort_id}, not {data.cohort_id} (R1)."
                    ),
                ),
            )

    wanted = {
        "input": [data.input_file_id] if data.input_file_id else [],
        "predictions": [v.predictions_file_id for v in data.variants if v.predictions_file_id],
    }
    ids = {file_id for group in wanted.values() for file_id in group}
    artifacts: dict[UUID, Any] = {}
    if ids:
        artifacts = {
            row.file_id: row
            for row in (
                await db.execute(
                    select(
                        StoredFile.file_id, StoredFile.session_id, StoredFile.kind, StoredFile.sha256
                    ).where(StoredFile.file_id.in_(ids))
                )
            ).all()
        }
    for role, kind in (("input", "input_csv"), ("predictions", "export_csv")):
        for file_id in wanted[role]:
            artifact = artifacts.get(file_id)
            if artifact is None:
                raise await _abort(
                    db, HTTPException(status_code=422, detail=f"Stored file {file_id} not found")
                )
            if artifact.session_id != run.session_id or artifact.kind != kind:
                raise await _abort(
                    db,
                    HTTPException(
                        status_code=409,
                        detail=(
                            f"The {role} stored file must be a {kind} of session "
                            f"{run.session_id} (R1/R6)."
                        ),
                    ),
                )
    # the digest verifies the captured bytes when they were the only input.
    if (
        data.input_file_id is not None
        and not data.split_definition.get("holdout_artifact_id")
        and artifacts[data.input_file_id].sha256 != data.dataset_digest
    ):
        raise await _abort(
            db,
            HTTPException(
                status_code=409,
                detail=(
                    "dataset_digest does not match the captured input stored file; the "
                    "recorded input must be exactly the bytes the model trained on."
                ),
            ),
        )


async def record_comparison(
    db: AsyncSession,
    run_id: UUID,
    comparison_key: str,
    data: schemas.ModelComparisonWrite,
) -> tuple[dict[str, Any], bool]:
    if not comparison_key.strip() or "/" in comparison_key:
        raise HTTPException(status_code=422, detail="comparison_key must be non-blank and contain no '/'")
    run = await require_producer_run(db, run_id, MODELING_AGENT_TYPE, "Model results")
    session_id = run.session_id
    await lock_session(db, session_id)
    await _check_inputs(db, run, data)

    existing = {
        row.variant_key: row
        for row in (
            await db.execute(
                select(ModelResult).where(
                    ModelResult.created_by_run_id == run_id,
                    ModelResult.comparison_key == comparison_key,
                )
            )
        ).scalars().all()
    }
    differing = sorted(
        variant.variant_key
        for variant in data.variants
        if variant.variant_key in existing
        and _row_payload(existing[variant.variant_key]) != _write_payload(data, variant)
    )
    if differing:
        raise await _abort(
            db,
            HTTPException(
                status_code=409,
                detail={
                    "code": "reconciliation_required",
                    "message": (
                        f"Comparison {comparison_key!r} of this run already records "
                        f"{len(differing)} of these variant(s) with different content. "
                        "Identical content is reused; a changed result is never silently "
                        "accepted."
                    ),
                    "differing_variants": differing[:20],
                },
            ),
        )
    fresh = [variant for variant in data.variants if variant.variant_key not in existing]
    if not fresh:
        await db.commit()
        return await get_comparison(db, run_id, comparison_key), False

    rows = [
        ModelResult(
            created_by_run_id=run_id,
            comparison_key=comparison_key,
            variant_key=variant.variant_key,
            **{field: getattr(data, field) for field in _SHARED_FIELDS},
            **{field: getattr(variant, field) for field in _VARIANT_FIELDS},
        )
        for variant in fresh
    ]
    selected = next((variant.variant_key for variant in fresh if variant.selected), None)
    async with publication(db, session_id) as publish:
        db.add_all(rows)
        try:
            await db.flush()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail=f"Model results rejected: {exc.orig}") from exc
        publish(
            "model_results_recorded",
            {
                "schema_version": 1,
                "comparison_key": comparison_key,
                "created_by_run_id": str(run_id),
                "feature_set_id": str(data.feature_set_id) if data.feature_set_id else None,
                "variant_count": len(rows),
                "selected_variant_key": selected,
            },
        )
    return await get_comparison(db, run_id, comparison_key), True
