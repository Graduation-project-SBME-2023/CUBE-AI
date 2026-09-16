"""Cohort repository"""

from __future__ import annotations

import copy
import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from application_api.db.models.agent_runs import AgentRun
from application_api.db.models.cohorts import Cohort, CohortMember
from application_api.db.models.steps import PlanStep
from application_api.db.models.study import Study
from application_api.db.models.volume import Volume
from application_api.repositories import agent_runs as runs_repo
from application_api.schemas import cohorts as schemas
from application_api.services.publication import lock_session, publication

logger = logging.getLogger(__name__)

POPULATION_AGENT_TYPE = "study_population_planner_agent"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _versioned(document: dict[str, Any] | None) -> dict[str, Any]:
    merged = copy.deepcopy(document or {})
    merged["schema_version"] = schemas.COHORT_SCHEMA_VERSION
    return merged


def _frozen_conflict(cohort: Cohort) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=(
            f"Cohort {cohort.cohort_id} was frozen at {cohort.frozen_at.isoformat()} "
            "and is immutable. Changing an approved cohort needs a new session."
        ),
    )


async def _abort(db: AsyncSession, exc: HTTPException) -> HTTPException:
    """Roll back and return an exception that was built BEFORE the rollback."""
    await db.rollback()
    return exc


async def _commit(db: AsyncSession, what: str) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"{what} rejected: {exc.orig}") from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------
async def get_cohort(db: AsyncSession, cohort_id: UUID) -> Cohort:
    cohort = (
        await db.execute(select(Cohort).where(Cohort.cohort_id == cohort_id))
    ).scalar_one_or_none()
    if cohort is None:
        raise HTTPException(status_code=404, detail=f"Cohort {cohort_id} not found")
    return cohort


async def find_cohort(db: AsyncSession, agent_run_id: UUID, cohort_key: str) -> Cohort | None:
    result = await db.execute(
        select(Cohort).where(
            Cohort.created_by_agent_run_id == agent_run_id,
            Cohort.cohort_key == cohort_key,
        )
    )
    return result.scalar_one_or_none()


async def list_session_cohorts(db: AsyncSession, session_id: UUID) -> list[Cohort]:
    result = await db.execute(
        select(Cohort)
        .join(AgentRun, AgentRun.agent_run_id == Cohort.created_by_agent_run_id)
        .join(PlanStep, PlanStep.step_id == AgentRun.step_id)
        .where(AgentRun.session_id == session_id)
        .order_by(PlanStep.step_order, Cohort.created_at)
    )
    return list(result.scalars().all())


async def population_run(db: AsyncSession, session_id: UUID) -> AgentRun:
    run, _ = await runs_repo.resolve_run_for_agent_type(db, session_id, POPULATION_AGENT_TYPE)
    if run is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Session {session_id} has no approved population step. A cohort "
                "belongs to the run that produces it, so the plan must be approved first."
            ),
        )
    return run


async def get_population_cohort(db: AsyncSession, session_id: UUID) -> Cohort | None:
    run = await population_run(db, session_id)
    return await find_cohort(db, run.agent_run_id, schemas.POPULATION_COHORT_KEY)


def _analysis_rows(cohort: Cohort) -> list[dict[str, Any]]:
    field = cohort.label_definition.get("field", "has_target_disease")
    positive = int(cohort.label_definition.get("positive_target", 1))
    negative = int(cohort.label_definition.get("negative_target", 0))
    rows = []
    for member in sorted(cohort.members, key=lambda item: item.ordinal):
        value = (member.label or {}).get(field)
        if not isinstance(value, bool):
            # Unlabelled is pending/excluded, never a negative.
            continue
        rows.append(
            {
                "member_id": member.member_id,
                "ordinal": member.ordinal,
                "subject_key": member.subject_key,
                "study_uid": member.study_uid,
                "volume_series_instance_uid": member.volume_series_instance_uid,
                "target": positive if value else negative,
                "label_source": member.label_source,
                "label_confidence": member.label_confidence,
            }
        )
    return rows


async def analysis_rows(db: AsyncSession, cohort_id: UUID) -> list[dict[str, Any]]:
    """Labelled members in `ordinal` order — the generated `targets` view."""
    return _analysis_rows(await get_cohort(db, cohort_id))


def analysis_rows_csv(rows: list[dict[str, Any]]) -> str:
    """A generated export for tools that consume a CSV. Never written to disk
    by the API and never authoritative."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "name", "target", "subject_key", "ordinal"])
    for row in rows:
        writer.writerow(
            [
                row["volume_series_instance_uid"],
                row["subject_key"],
                row["target"],
                row["subject_key"],
                row["ordinal"],
            ]
        )
    return buffer.getvalue()


# ----------------------------------------------------------------------
# Writes
# ----------------------------------------------------------------------
async def _locked_cohort(db: AsyncSession, cohort_id: UUID) -> Cohort:
    """Lock order: session ->... -> domain parent -> member."""
    cohort = await get_cohort(db, cohort_id)
    run = await runs_repo.get_run(db, cohort.created_by_agent_run_id)
    await lock_session(db, run.session_id)
    await db.execute(
        text("SELECT cohort_id FROM cohorts WHERE cohort_id = :cid FOR UPDATE"),
        {"cid": str(cohort_id)},
    )
    await db.refresh(cohort, attribute_names=["frozen_at", "criteria", "selection_summary"])
    return cohort


async def upsert_cohort(
    db: AsyncSession,
    agent_run_id: UUID,
    cohort_key: str,
    data: schemas.CohortUpsert,
) -> tuple[Cohort, bool]:
    """Create the draft cohort, or merge into it while it is not frozen."""
    run = await runs_repo.get_run(db, agent_run_id)
    await lock_session(db, run.session_id)

    cohort = await find_cohort(db, agent_run_id, cohort_key)
    if cohort is None:
        cohort = Cohort(
            created_by_agent_run_id=agent_run_id,
            cohort_key=cohort_key,
            criteria=_versioned(data.criteria),
            label_definition=_versioned(data.label_definition or schemas.DEFAULT_LABEL_DEFINITION),
            selection_summary=_versioned(data.selection_summary) if data.selection_summary else None,
        )
        db.add(cohort)
        await _commit(db, "Cohort")
        return await get_cohort(db, cohort.cohort_id), True

    criteria = copy.deepcopy(cohort.criteria)
    criteria.update(data.criteria or {})
    summary = copy.deepcopy(cohort.selection_summary or {})
    summary.update(data.selection_summary or {})
    definition = (
        _versioned(data.label_definition)
        if data.label_definition is not None
        else cohort.label_definition
    )
    changed = (
        _versioned(criteria) != cohort.criteria
        or definition != cohort.label_definition
        or bool(data.selection_summary and _versioned(summary) != cohort.selection_summary)
    )
    if not changed:
        # Idempotent: releases the session lock without expiring the row.
        await db.commit()
        return cohort, False
    if cohort.frozen_at is not None:
        raise await _abort(db, _frozen_conflict(cohort))

    cohort.criteria = _versioned(criteria)
    cohort.label_definition = definition
    if data.selection_summary:
        cohort.selection_summary = _versioned(summary)
    await _commit(db, "Cohort")
    return cohort, False


async def set_members(db: AsyncSession, cohort_id: UUID, data: schemas.CohortMembersSet) -> Cohort:
    cohort = await _locked_cohort(db, cohort_id)
    if cohort.frozen_at is not None:
        raise await _abort(db, _frozen_conflict(cohort))

    uids = data.volume_series_instance_uids
    archive: dict[str, tuple[str, Any]] = {}
    if uids:
        result = await db.execute(
            select(Volume.volume_series_instance_uid, Volume.study_uid, Study.patient_id)
            .join(Study, Study.study_uid == Volume.study_uid)
            .where(Volume.volume_series_instance_uid.in_(uids))
        )
        archive = {row[0]: (row[1], row[2]) for row in result.all()}
    unknown = [uid for uid in uids if uid not in archive]
    if unknown:
        raise await _abort(
            db,
            HTTPException(
                status_code=422,
                detail=(
                    f"{len(unknown)} volume(s) are not in the imaging archive and cannot "
                    f"join a cohort: {unknown[:10]}"
                ),
            ),
        )

    existing = {member.volume_series_instance_uid: member for member in cohort.members}
    wanted = set(uids)
    try:
        await db.execute(text("SET CONSTRAINTS uq_cohort_members_cohort_id_ordinal DEFERRED"))
        removed = [uid for uid in existing if uid not in wanted]
        if removed:
            await db.execute(
                delete(CohortMember).where(
                    CohortMember.cohort_id == cohort_id,
                    CohortMember.volume_series_instance_uid.in_(removed),
                )
            )
        for ordinal, uid in enumerate(uids, start=1):
            member = existing.get(uid)
            if member is not None:
                if member.ordinal != ordinal:
                    member.ordinal = ordinal
                continue
            study_uid, patient_id = archive[uid]
            db.add(
                CohortMember(
                    cohort_id=cohort_id,
                    study_uid=study_uid,
                    volume_series_instance_uid=uid,
                    # The patient is the split group: two series of one patient
                    # must never straddle a train/holdout boundary.
                    subject_key=str(patient_id) if patient_id else f"volume:{uid}",
                    ordinal=ordinal,
                    label_source="pending",
                )
            )
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Cohort membership rejected: {exc.orig}") from exc
    await _commit(db, "Cohort membership")
    db.expunge_all()
    return await get_cohort(db, cohort_id)


async def write_labels(db: AsyncSession, cohort_id: UUID, data: schemas.CohortLabelsWrite) -> Cohort:
    cohort = await _locked_cohort(db, cohort_id)
    if cohort.frozen_at is not None:
        raise await _abort(db, _frozen_conflict(cohort))

    field = cohort.label_definition.get("field", "has_target_disease")
    members = {member.volume_series_instance_uid: member for member in cohort.members}
    unknown = [
        item.volume_series_instance_uid
        for item in data.labels
        if item.volume_series_instance_uid not in members
    ]
    if unknown:
        raise await _abort(
            db,
            HTTPException(
                status_code=422,
                detail=f"{len(unknown)} label(s) name volumes outside this cohort: {unknown[:10]}",
            ),
        )
    if any(item.label_source == "pending" and item.has_target_disease is not None for item in data.labels):
        raise await _abort(
            db,
            HTTPException(status_code=422, detail="A pending label cannot carry a value; name its source."),
        )

    for item in data.labels:
        member = members[item.volume_series_instance_uid]
        member.label = None if item.has_target_disease is None else {field: item.has_target_disease}
        member.label_source = item.label_source
        member.label_confidence = item.label_confidence
        member.label_provenance = item.label_provenance
        if item.clinical_data is not None:
            member.clinical_data = item.clinical_data
    await _commit(db, "Cohort labels")
    db.expunge_all()
    return await get_cohort(db, cohort_id)


async def freeze_cohort(db: AsyncSession, cohort_id: UUID) -> tuple[Cohort, Any]:
    """The doctor's approval (the HITL gate) — one-way."""
    cohort = await _locked_cohort(db, cohort_id)
    if cohort.frozen_at is not None:
        await db.commit()
        return cohort, None
    run = await runs_repo.get_run(db, cohort.created_by_agent_run_id)
    session_id = run.session_id
    total = len(cohort.members)
    labeled = len(_analysis_rows(cohort))
    if total == 0:
        raise await _abort(
            db,
            HTTPException(
                status_code=400,
                detail=(
                    "Cannot approve: no volumes in the cohort. The population agent must "
                    "discover volumes first."
                ),
            ),
        )
    if labeled == 0:
        raise await _abort(
            db,
            HTTPException(
                status_code=400,
                detail=(
                    "Cannot approve: no volumes have valid labels. Please label at least "
                    "one volume in the Patient Labeling tab."
                ),
            ),
        )

    event = None
    async with publication(db, session_id) as publish:
        cohort.frozen_at = _now()
        await db.flush()
        event = publish(
            "cohort_approval_status",
            {
                "status": "approved",
                "cohort_id": str(cohort_id),
                "labeled_count": labeled,
                "total_count": total,
            },
        )
    return cohort, event
