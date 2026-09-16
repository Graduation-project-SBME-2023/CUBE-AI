"""Segmentation batch repository"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.agent_runs import AgentRun
from application_api.db.models.cohorts import CohortMember
from application_api.db.models.model import Model
from application_api.db.models.segmentation_runs import (
    SegmentationPair,
    SegmentationRun,
    SegmentationTarget,
)
from application_api.db.models.steps import PlanStep
from application_api.repositories import agent_runs as runs_repo
from application_api.repositories import cohorts as cohorts_repo
from application_api.schemas import segmentation_runs as schemas
from application_api.services.publication import lock_session, publication

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _commit(db: AsyncSession, what: str) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"{what} rejected: {exc.orig}") from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc


async def _abort(db: AsyncSession, exc: HTTPException) -> HTTPException:
    """Roll back and return an exception built BEFORE the rollback (a rollback
    expires every loaded object, so formatting from one afterwards would
    lazy-load outside the async context)."""
    await db.rollback()
    return exc


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------
async def _run_or_404(db: AsyncSession, segmentation_run_id: UUID) -> SegmentationRun:
    run = (
        await db.execute(
            select(SegmentationRun).where(
                SegmentationRun.segmentation_run_id == segmentation_run_id
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"Segmentation run {segmentation_run_id} not found"
        )
    return run


async def find_batch(
    db: AsyncSession, agent_run_id: UUID, batch_key: str
) -> SegmentationRun | None:
    return (
        await db.execute(
            select(SegmentationRun).where(
                SegmentationRun.created_by_agent_run_id == agent_run_id,
                SegmentationRun.batch_key == batch_key,
            )
        )
    ).scalar_one_or_none()


async def _targets(db: AsyncSession, segmentation_run_id: UUID) -> list[SegmentationTarget]:
    result = await db.execute(
        select(SegmentationTarget)
        .where(SegmentationTarget.segmentation_run_id == segmentation_run_id)
        .order_by(SegmentationTarget.target_key)
    )
    return list(result.scalars().all())


async def _pair_rows(db: AsyncSession, segmentation_run_id: UUID):
    result = await db.execute(
        select(
            SegmentationPair,
            SegmentationTarget.target_key,
            CohortMember.subject_key,
            CohortMember.ordinal,
        )
        .join(SegmentationTarget, SegmentationTarget.target_id == SegmentationPair.target_id)
        .join(CohortMember, CohortMember.member_id == SegmentationPair.cohort_member_id)
        .where(SegmentationPair.segmentation_run_id == segmentation_run_id)
        .order_by(CohortMember.ordinal, SegmentationTarget.target_key)
    )
    return result.all()


def _pair_out(pair: SegmentationPair, target_key: str, subject_key=None, ordinal=None) -> dict:
    return {
        "pair_id": pair.pair_id,
        "pair_key": schemas.pair_key(pair.volume_series_instance_uid, target_key),
        "segmentation_run_id": pair.segmentation_run_id,
        "target_id": pair.target_id,
        "target_key": target_key,
        "volume_series_instance_uid": pair.volume_series_instance_uid,
        "cohort_id": pair.cohort_id,
        "cohort_member_id": pair.cohort_member_id,
        "subject_key": subject_key,
        "ordinal": ordinal,
        "external_job_id": pair.external_job_id,
        "status": pair.status,
        "candidate_segmentation_uid": pair.candidate_segmentation_uid,
        "selected_segmentation_uid": pair.selected_segmentation_uid,
        "segment_number": pair.segment_number,
        "review_decision": pair.review_decision,
        "quality": pair.quality,
        "created_at": pair.created_at,
    }


async def batch_out(db: AsyncSession, run: SegmentationRun) -> dict[str, Any]:
    agent_run = await runs_repo.get_run(db, run.created_by_agent_run_id)
    return {
        "segmentation_run_id": run.segmentation_run_id,
        "created_by_agent_run_id": run.created_by_agent_run_id,
        "session_id": agent_run.session_id,
        "cohort_id": run.cohort_id,
        "batch_key": run.batch_key,
        "config": run.config,
        "review_status": run.review_status,
        "review_request_hash": run.review_request_hash,
        "reviewed_at": run.reviewed_at,
        "created_at": run.created_at,
        "targets": await _targets(db, run.segmentation_run_id),
        "pairs": [
            _pair_out(pair, target_key, subject_key, ordinal)
            for pair, target_key, subject_key, ordinal in await _pair_rows(
                db, run.segmentation_run_id
            )
        ],
    }


async def get_batch(db: AsyncSession, segmentation_run_id: UUID) -> dict[str, Any]:
    return await batch_out(db, await _run_or_404(db, segmentation_run_id))


async def list_session_batches(db: AsyncSession, session_id: UUID) -> list[dict[str, Any]]:
    result = await db.execute(
        select(SegmentationRun)
        .join(AgentRun, AgentRun.agent_run_id == SegmentationRun.created_by_agent_run_id)
        .join(PlanStep, PlanStep.step_id == AgentRun.step_id)
        .where(AgentRun.session_id == session_id)
        .order_by(PlanStep.step_order, SegmentationRun.created_at)
    )
    return [await batch_out(db, run) for run in result.scalars().all()]


async def reviewed_masks(db: AsyncSession, session_id: UUID) -> list[dict[str, Any]]:
    """Accepted and modified pairs of this session's submitted reviews."""
    rows = []
    for batch in await list_session_batches(db, session_id):
        if batch["review_status"] != "submitted":
            continue
        for pair in batch["pairs"]:
            if pair["status"] == "ready" and pair["review_decision"] in {"accepted", "modified"}:
                rows.append({**pair, "cohort_id": batch["cohort_id"]})
    return rows


# ----------------------------------------------------------------------
# Create (insert-or-read)
# ----------------------------------------------------------------------
async def create_batch(
    db: AsyncSession,
    agent_run_id: UUID,
    batch_key: str,
    data: schemas.SegmentationBatchCreate,
) -> tuple[dict[str, Any], bool]:
    agent_run = await runs_repo.get_run(db, agent_run_id)
    await lock_session(db, agent_run.session_id)

    existing = await find_batch(db, agent_run_id, batch_key)
    if existing is not None:
        if existing.config != data.config or existing.cohort_id != data.cohort_id:
            raise await _abort(db, HTTPException(
                status_code=409,
                detail={
                    "code": "reconciliation_required",
                    "message": (
                        f"Batch {batch_key!r} of run {agent_run_id} already exists with a "
                        "different cohort or config. The stored batch is immutable; it is "
                        "read back, never rewritten."
                    ),
                    "segmentation_run_id": str(existing.segmentation_run_id),
                },
            ))
        await db.commit()
        return await batch_out(db, existing), False

    cohort = await cohorts_repo.get_cohort(db, data.cohort_id)
    members = {member.volume_series_instance_uid: member for member in cohort.members}
    field = cohort.label_definition.get("field", "has_target_disease")

    target_keys = [target.target_key for target in data.targets]
    if len(set(target_keys)) != len(target_keys):
        raise await _abort(db, HTTPException(status_code=422, detail="target_key values must be unique"))
    known_targets = set(target_keys)

    names = {target.model_name for target in data.targets if target.model_name}
    model_ids: dict[str, UUID] = {}
    if names:
        result = await db.execute(select(Model.name, Model.model_id).where(Model.name.in_(names)))
        model_ids = {name: model_id for name, model_id in result.all()}

    for pair in data.pairs:
        if pair.target_key not in known_targets:
            raise await _abort(
                db,
                HTTPException(
                    status_code=422, detail=f"Pair names unknown target {pair.target_key!r}"
                ),
            )
        member = members.get(pair.volume_series_instance_uid)
        if member is None:
            raise await _abort(
                db,
                HTTPException(
                    status_code=422,
                    detail=(
                        f"Volume {pair.volume_series_instance_uid} is not a member of cohort "
                        f"{data.cohort_id}; a segmentation pair must be a cohort member (R1)."
                    ),
                ),
            )
        if not isinstance((member.label or {}).get(field), bool):
            raise await _abort(
                db,
                HTTPException(
                    status_code=422,
                    detail=(
                        f"Volume {pair.volume_series_instance_uid} has no valid label; an "
                        "unlabelled member is pending/excluded and is not segmented."
                    ),
                ),
            )

    try:
        run = SegmentationRun(
            created_by_agent_run_id=agent_run_id,
            cohort_id=cohort.cohort_id,
            batch_key=batch_key,
            config=data.config,
            review_status="not_ready",
        )
        db.add(run)
        # The insert trigger checks same-session alignment and freezes the
        # cohort on this first downstream use.
        await db.flush()

        target_ids: dict[str, UUID] = {}
        for target in data.targets:
            row = SegmentationTarget(
                segmentation_run_id=run.segmentation_run_id,
                target_key=target.target_key,
                label=target.label,
                specification=target.specification,
                model_id=model_ids.get(target.model_name or ""),
            )
            db.add(row)
            await db.flush()
            target_ids[target.target_key] = row.target_id

        for pair in data.pairs:
            member = members[pair.volume_series_instance_uid]
            db.add(
                SegmentationPair(
                    segmentation_run_id=run.segmentation_run_id,
                    target_id=target_ids[pair.target_key],
                    volume_series_instance_uid=pair.volume_series_instance_uid,
                    cohort_id=cohort.cohort_id,
                    cohort_member_id=member.member_id,
                    external_job_id=pair.external_job_id,
                    status=pair.status,
                    candidate_segmentation_uid=pair.candidate_segmentation_uid,
                    segment_number=1,
                    quality=pair.quality,
                )
            )
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Segmentation batch rejected: {exc.orig}") from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    await _commit(db, "Segmentation batch")
    return await batch_out(db, await _run_or_404(db, run.segmentation_run_id)), True


# ----------------------------------------------------------------------
# Progress
# ----------------------------------------------------------------------
async def _locked_batch(db: AsyncSession, segmentation_run_id: UUID) -> SegmentationRun:
    run = await _run_or_404(db, segmentation_run_id)
    agent_run = await runs_repo.get_run(db, run.created_by_agent_run_id)
    await lock_session(db, agent_run.session_id)
    await db.execute(
        text(
            "SELECT segmentation_run_id FROM segmentation_runs "
            "WHERE segmentation_run_id = :rid FOR UPDATE"
        ),
        {"rid": str(segmentation_run_id)},
    )
    await db.refresh(run, attribute_names=["review_status", "review_request_hash", "reviewed_at"])
    return run


async def _pairs_by_key(
    db: AsyncSession, segmentation_run_id: UUID
) -> tuple[dict[str, SegmentationPair], dict[str, str]]:
    """Pairs keyed by pair key, plus a volume -> key map for single-target
    batches (the review UI has historically sent a bare volume UID then)."""
    by_key: dict[str, SegmentationPair] = {}
    by_volume: dict[str, list[str]] = {}
    for pair, target_key, _, _ in await _pair_rows(db, segmentation_run_id):
        key = schemas.pair_key(pair.volume_series_instance_uid, target_key)
        by_key[key] = pair
        by_volume.setdefault(pair.volume_series_instance_uid, []).append(key)
    single = {volume: keys[0] for volume, keys in by_volume.items() if len(keys) == 1}
    return by_key, single


def _resolve_keys(values, by_key, by_volume) -> set[str]:
    resolved = set()
    for value in values or []:
        key = str(value)
        if key in by_key:
            resolved.add(key)
        elif key in by_volume:
            resolved.add(by_volume[key])
        else:
            raise HTTPException(
                status_code=400, detail=f"Review contains unknown segmentation pair {key!r}"
            )
    return resolved


async def update_pair_states(
    db: AsyncSession, segmentation_run_id: UUID, data: schemas.PairStatesUpdate
) -> dict[str, Any]:
    run = await _locked_batch(db, segmentation_run_id)
    if run.review_status in {"submitting", "submitted"}:
        raise await _abort(
            db,
            HTTPException(
                status_code=409,
                detail=f"Segmentation run {segmentation_run_id} review is {run.review_status}; "
                "its pairs no longer change.",
            ),
        )
    by_key, _ = await _pairs_by_key(db, segmentation_run_id)
    for update in data.pairs:
        pair = by_key.get(update.pair_key)
        if pair is None:
            raise await _abort(db, HTTPException(
                status_code=404, detail=f"Unknown segmentation pair {update.pair_key!r}"
            ))
        pair.status = update.status
        pair.quality = update.quality
        pair.candidate_segmentation_uid = update.candidate_segmentation_uid
        if update.external_job_id is not None:
            if pair.external_job_id not in (None, update.external_job_id):
                raise await _abort(db, HTTPException(
                    status_code=409,
                    detail=(
                        f"Pair {update.pair_key!r} already reconciles against MONAI job "
                        f"{pair.external_job_id}; a receipt pointer is never re-pointed."
                    ),
                ))
            pair.external_job_id = update.external_job_id
        if update.status != "ready" and pair.review_decision is not None:
            # A pair re-entering processing (its preview must be rebuilt) has no
            # decision to keep: the reviewer decided about a result that is
            # being regenerated.
            pair.review_decision = None
    if data.review_status is not None and data.review_status != run.review_status:
        run.review_status = data.review_status
    await _commit(db, "Segmentation pair progress")
    return await batch_out(db, await _run_or_404(db, segmentation_run_id))


# ----------------------------------------------------------------------
# Review
# ----------------------------------------------------------------------
async def save_draft(
    db: AsyncSession, segmentation_run_id: UUID, data: schemas.ReviewDraft
) -> dict[str, Any]:
    run = await _locked_batch(db, segmentation_run_id)
    if run.review_status != "pending":
        raise await _abort(db, HTTPException(
            status_code=409,
            detail=f"Segmentation run {segmentation_run_id} is {run.review_status}; drafts "
            "are only accepted while the review is pending.",
        ))
    by_key, by_volume = await _pairs_by_key(db, segmentation_run_id)
    for raw_key, decision in data.decisions.items():
        (key,) = _resolve_keys([raw_key], by_key, by_volume)
        pair = by_key[key]
        if pair.status != "ready":
            raise await _abort(db, HTTPException(
                status_code=400, detail=f"Pair {key!r} is {pair.status}, not ready for review"
            ))
        # A draft decision has no final selected output.
        pair.review_decision = decision
        pair.selected_segmentation_uid = None
    await _commit(db, "Review draft")
    return await batch_out(db, await _run_or_404(db, segmentation_run_id))


async def begin_review(
    db: AsyncSession, segmentation_run_id: UUID, request_hash: str
) -> dict[str, Any]:
    run = await _locked_batch(db, segmentation_run_id)
    if run.review_status in {"submitting", "submitted"}:
        if run.review_request_hash != request_hash:
            raise await _abort(db, HTTPException(
                status_code=409,
                detail=(
                    f"Segmentation run {segmentation_run_id} is already {run.review_status} "
                    "with a different review. One final submission per batch."
                ),
            ))
        await db.commit()
        return await batch_out(db, run)
    if run.review_status != "pending":
        raise await _abort(db, HTTPException(
            status_code=409,
            detail=f"Segmentation run {segmentation_run_id} is not awaiting review.",
        ))
    run.review_status = "submitting"
    run.review_request_hash = request_hash
    await _commit(db, "Review submission")
    return await batch_out(db, await _run_or_404(db, segmentation_run_id))


async def abort_review(
    db: AsyncSession, segmentation_run_id: UUID, request_hash: str
) -> dict[str, Any]:
    """Return a stuck `submitting` review to `pending` (for example after the
    edited-mask publication failed), so the reviewer can change decisions."""
    run = await _locked_batch(db, segmentation_run_id)
    if run.review_status != "submitting" or run.review_request_hash != request_hash:
        raise await _abort(db, HTTPException(
            status_code=409,
            detail=f"Segmentation run {segmentation_run_id} is not submitting that review.",
        ))
    run.review_status = "pending"
    run.review_request_hash = None
    await _commit(db, "Review abort")
    return await batch_out(db, await _run_or_404(db, segmentation_run_id))


async def complete_review(
    db: AsyncSession, segmentation_run_id: UUID, data: schemas.ReviewSubmission
) -> tuple[dict[str, Any], Any]:
    request_hash = data.request_hash()
    run = await _locked_batch(db, segmentation_run_id)
    if run.review_status == "submitted":
        if run.review_request_hash == request_hash:
            await db.commit()
            return await batch_out(db, run), None
        raise await _abort(
            db,
            HTTPException(
                status_code=409,
                detail=(
                    f"Segmentation run {segmentation_run_id} was already submitted with a "
                    "different review."
                ),
            ),
        )
    if run.review_status != "submitting" or run.review_request_hash != request_hash:
        raise await _abort(db, HTTPException(
            status_code=409,
            detail=(
                "Begin the submission with this review's request hash before completing "
                "it; a different review is already in flight or none was begun."
            ),
        ))

    by_key, by_volume = await _pairs_by_key(db, segmentation_run_id)
    ready = {key for key, pair in by_key.items() if pair.status == "ready"}
    modified = _resolve_keys(data.modified, by_key, by_volume) & ready
    rejected = _resolve_keys(data.rejected, by_key, by_volume) & ready
    accepted = _resolve_keys(data.accepted, by_key, by_volume) & ready
    # Explicit negative decisions beat the default "accepted": modified is the
    # strongest signal, then rejected. Undecided ready pairs are rejected.
    accepted -= modified | rejected
    rejected -= modified
    rejected |= ready - (accepted | modified | rejected)

    selected: dict[str, str] = {}
    for raw_key, uid in data.selected.items():
        (key,) = _resolve_keys([raw_key], by_key, by_volume)
        selected[key] = uid
    missing = sorted(key for key in modified if not selected.get(key))
    if missing:
        raise await _abort(db, HTTPException(
            status_code=422,
            detail=(
                f"{len(missing)} modified pair(s) name no published SEG. An edited mask "
                f"is published to Orthanc before its review is final: {missing[:5]}"
            ),
        ))

    event = None
    agent_run = await runs_repo.get_run(db, run.created_by_agent_run_id)
    async with publication(db, agent_run.session_id) as publish:
        for key, pair in by_key.items():
            if key in modified:
                pair.review_decision = "modified"
                pair.selected_segmentation_uid = selected[key]
            elif key in accepted:
                pair.review_decision = "accepted"
                pair.selected_segmentation_uid = pair.candidate_segmentation_uid
            elif key in rejected:
                pair.review_decision = "rejected"
                pair.selected_segmentation_uid = None
            else:
                pair.review_decision = None
                pair.selected_segmentation_uid = None
        try:
            await db.flush()
            run.review_status = "submitted"
            run.reviewed_at = _now()
            await db.flush()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail=f"Review rejected by the database: {exc.orig}"
            ) from exc
        event = publish(
            "segmentation_review_submitted",
            {
                "schema_version": 1,
                "segmentation_run_id": str(run.segmentation_run_id),
                "accepted": sorted(accepted),
                "modified": sorted(modified),
                "rejected": sorted(rejected),
            },
        )
    return await batch_out(db, await _run_or_404(db, segmentation_run_id)), event
