"""Agent-run repository"""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from application_api.db.models.agent_runs import AgentRun
from application_api.db.models.steps import PlanStep
from application_api.schemas import agent_runs as schemas
from application_api.services.publication import lock_session, publication

logger = logging.getLogger(__name__)

SCRATCHPAD_SCHEMA_VERSION = schemas.SCRATCHPAD_SCHEMA_VERSION

# The UI speaks in upper-case agent statuses; the database speaks in the four
# lower-case values defines. One mapping, in one place.
_UI_STATUS = {
    "pending": "PENDING",
    "running": "RUNNING",
    "completed": "COMPLETED",
    "failed": "FAILED",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------
async def list_runs(db: AsyncSession, session_id: UUID) -> list[AgentRun]:
    result = await db.execute(
        select(AgentRun)
        .options(selectinload(AgentRun.step))
        .join(PlanStep, PlanStep.step_id == AgentRun.step_id)
        .where(AgentRun.session_id == session_id)
        .order_by(PlanStep.step_order)
    )
    return list(result.unique().scalars().all())


async def get_run(db: AsyncSession, agent_run_id: UUID) -> AgentRun:
    run = (
        await db.execute(
            select(AgentRun)
            .options(selectinload(AgentRun.step))
            .where(AgentRun.agent_run_id == agent_run_id)
        )
    ).unique().scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"Agent run {agent_run_id} not found")
    return run


async def resolve_run_for_agent_type(
    db: AsyncSession, session_id: UUID, agent_type: str
) -> tuple[AgentRun | None, int]:
    """Several steps may legitimately name one agent."""
    runs = [run for run in await list_runs(db, session_id) if run.step.agent_type == agent_type]
    return (runs[0] if runs else None), len(runs)


def agent_status_payload(runs: list[AgentRun]) -> dict[str, Any]:
    """The `pipeline_agent_status` payload, derived from the runs themselves."""
    return {
        "schema_version": 1,
        "agents": [
            {
                "name": run.step.agent_type,
                "title": run.step.title,
                "step_id": str(run.step_id),
                "step_order": run.step.step_order,
                "agent_run_id": str(run.agent_run_id),
                "status": _UI_STATUS[run.status],
                "started_at": _iso(run.started_at),
                "finished_at": _iso(run.completed_at),
                "error": (run.error or {}).get("message") if run.error else None,
            }
            for run in runs
        ],
    }


# ----------------------------------------------------------------------
# Admission — called inside the approval transaction only
# ----------------------------------------------------------------------
async def create_pending_runs(db: AsyncSession, steps: list[PlanStep]) -> list[AgentRun]:
    runs = [
        AgentRun(step_id=step.step_id, session_id=step.session_id, status="pending")
        for step in steps
    ]
    for run in runs:
        db.add(run)
    await db.flush()
    return runs


# ----------------------------------------------------------------------
# Transitions
# ----------------------------------------------------------------------
async def _locked_run(db: AsyncSession, agent_run_id: UUID) -> AgentRun:
    """Lock order: session → plan → step/run."""
    run = await get_run(db, agent_run_id)
    await lock_session(db, run.session_id)
    await db.execute(
        text("SELECT agent_run_id FROM agent_runs WHERE agent_run_id = :rid FOR UPDATE"),
        {"rid": str(agent_run_id)},
    )
    await db.refresh(run)
    return run


async def start_run(
    db: AsyncSession, agent_run_id: UUID, data: schemas.AgentRunStart
) -> tuple[AgentRun, Any]:
    run = await _locked_run(db, agent_run_id)

    if run.status == "completed":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Agent run {agent_run_id} already completed. V1 permits one run "
                "per step; a re-execution requires a new session."
            ),
        )
    if run.status == "running":
        # Crash recovery reclaims the same run. Identical content is reuse;
        # different content under the same key is a conflict, never a rewrite.
        conflicting = run.compiled_prompt != data.compiled_prompt
        await db.rollback()
        if conflicting:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Agent run {agent_run_id} is already running with a different "
                    "compiled prompt. Reconciliation is required; the compiled "
                    "prompt of a started run is immutable."
                ),
            )
        return await get_run(db, agent_run_id), None

    event = None

    async with publication(db, run.session_id) as publish:
        if run.status == "pending":
            run.compiled_prompt = data.compiled_prompt
            run.input_refs = data.input_refs
            run.started_at = _now()
        # A failed run resumes as the SAME run; its
        # compiled prompt and inputs stay exactly as they were.
        run.completed_at = None
        run.error = None
        run.status = "running"
        await db.flush()
        event = publish(
            "pipeline_agent_status",
            agent_status_payload(await list_runs(db, run.session_id)),
        )
    return await get_run(db, agent_run_id), event


async def complete_run(
    db: AsyncSession, agent_run_id: UUID, data: schemas.AgentRunComplete
) -> tuple[AgentRun, Any]:
    run = await _locked_run(db, agent_run_id)
    if run.status == "completed":
        await db.rollback()
        return await get_run(db, agent_run_id), None
    if run.status != "running":
        raise HTTPException(
            status_code=409,
            detail=f"Agent run {agent_run_id} is {run.status!r} and cannot complete.",
        )

    event = None
    async with publication(db, run.session_id) as publish:
        if data.output_summary is not None:
            merged = copy.deepcopy(run.output_summary or {"schema_version": 1})
            merged.update(data.output_summary)
            merged["schema_version"] = SCRATCHPAD_SCHEMA_VERSION
            run.output_summary = merged
        run.status = "completed"
        run.completed_at = _now()
        await db.flush()
        event = publish(
            "pipeline_agent_status",
            agent_status_payload(await list_runs(db, run.session_id)),
        )
    return await get_run(db, agent_run_id), event


async def fail_run(
    db: AsyncSession, agent_run_id: UUID, data: schemas.AgentRunFail
) -> tuple[AgentRun, Any]:
    run = await _locked_run(db, agent_run_id)
    event = None
    if run.status == "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Agent run {agent_run_id} completed; it cannot be marked failed.",
        )
    if run.status == "pending":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Agent run {agent_run_id} never started. A run that has not started "
                "stays pending; it is not a failure of anything."
            ),
        )

    async with publication(db, run.session_id) as publish:
        run.error = {
            "schema_version": 1,
            "code": data.code,
            "message": data.message,
            "details": data.details,
            "recorded_at": _iso(_now()),
        }
        run.status = "failed"
        run.completed_at = _now()
        await db.flush()
        event = publish(
            "pipeline_agent_status",
            agent_status_payload(await list_runs(db, run.session_id)),
        )
    return await get_run(db, agent_run_id), event


# ----------------------------------------------------------------------
# Scratchpad and agent output
# ----------------------------------------------------------------------
def _empty_notes(role: str) -> dict[str, Any]:
    return {
        "schema_version": SCRATCHPAD_SCHEMA_VERSION,
        "role": role,
        "status": "in_progress",
        "registered_at": _iso(_now()),
        "notes": [],
        "activity_log": [],
    }


def _log_activity(notes: dict[str, Any], action: str, detail: str) -> None:
    notes.setdefault("activity_log", []).append(
        {"action": action, "detail": detail, "timestamp": _iso(_now())}
    )


async def _mutate(db: AsyncSession, agent_run_id: UUID, change) -> AgentRun:
    """Serialise scratchpad/output writes on the run row, then commit."""
    run = await _locked_run(db, agent_run_id)
    if run.status == "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Agent run {agent_run_id} is completed and immutable.",
        )
    try:
        result = change(run)
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except (IntegrityError, SQLAlchemyError) as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    await db.refresh(run)
    return result if result is not None else run


async def scratchpad_register(
    db: AsyncSession, agent_run_id: UUID, data: schemas.ScratchpadRegister
) -> AgentRun:
    def change(run: AgentRun):
        if run.durable_notes and run.durable_notes.get("registered_at"):
            raise HTTPException(
                status_code=409,
                detail=f"Agent run {agent_run_id} is already registered.",
            )
        notes = _empty_notes(data.role)
        _log_activity(notes, "registered", f"Role: {data.role}")
        run.durable_notes = notes

    return await _mutate(db, agent_run_id, change)


def _require_registered(run: AgentRun) -> dict[str, Any]:
    notes = copy.deepcopy(run.durable_notes or {})
    if not notes.get("registered_at"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Agent run {run.agent_run_id} is not registered. Call "
                "scratchpad_register_agent first."
            ),
        )
    return notes


async def scratchpad_write_note(
    db: AsyncSession, agent_run_id: UUID, data: schemas.ScratchpadNote
) -> AgentRun:
    def change(run: AgentRun):
        notes = _require_registered(run)
        notes.setdefault("notes", []).append(
            {"content": data.content, "created_at": _iso(_now())}
        )
        _log_activity(notes, "wrote_note", f"Note #{len(notes['notes']) - 1}")
        run.durable_notes = notes

    return await _mutate(db, agent_run_id, change)


async def scratchpad_raise_flag(
    db: AsyncSession, agent_run_id: UUID, data: schemas.ScratchpadFlag
) -> AgentRun:
    def change(run: AgentRun):
        notes = _require_registered(run)
        warnings = copy.deepcopy(run.warnings or {"schema_version": 1, "items": []})
        warnings.setdefault("items", []).append(
            {
                "level": data.level,
                "message": data.message,
                "created_at": _iso(_now()),
                "source": "scratchpad_flag",
            }
        )
        _log_activity(
            notes, "raised_flag", f"[{data.level.upper()}] {data.message[:120]}"
        )
        run.warnings = warnings
        run.durable_notes = notes

    return await _mutate(db, agent_run_id, change)


async def scratchpad_set_status(
    db: AsyncSession, agent_run_id: UUID, data: schemas.ScratchpadSetStatus
) -> AgentRun:
    def change(run: AgentRun):
        notes = _require_registered(run)
        previous = notes.get("status")
        notes["status"] = data.status
        _log_activity(notes, "status_change", f"{previous} -> {data.status}")
        run.durable_notes = notes

    return await _mutate(db, agent_run_id, change)


async def scratchpad_write_summary(
    db: AsyncSession, agent_run_id: UUID, data: schemas.ScratchpadSummary
) -> AgentRun:
    def change(run: AgentRun):
        notes = _require_registered(run)
        summary = copy.deepcopy(run.output_summary or {"schema_version": SCRATCHPAD_SCHEMA_VERSION})
        if summary.get("scratchpad_summary") and not data.overwrite:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A summary already exists for this run. Set overwrite=true to "
                    "replace it."
                ),
            )
        summary["schema_version"] = SCRATCHPAD_SCHEMA_VERSION
        summary["scratchpad_summary"] = {
            "content": data.summary,
            "updated_at": _iso(_now()),
        }
        _log_activity(notes, "wrote_summary", f"{len(data.summary)} chars")
        run.output_summary = summary
        run.durable_notes = notes

    return await _mutate(db, agent_run_id, change)


async def write_agent_output(
    db: AsyncSession, agent_run_id: UUID, data: schemas.AgentOutputWrite
) -> AgentRun:
    """`write_agent_output` — the structured result, no longer a JSON file."""

    def change(run: AgentRun):
        summary = copy.deepcopy(run.output_summary or {"schema_version": SCRATCHPAD_SCHEMA_VERSION})
        summary["schema_version"] = SCRATCHPAD_SCHEMA_VERSION
        summary["agent_output"] = {
            "agent_role": data.agent_role,
            "status": data.status,
            "recorded_at": _iso(_now()),
            "inputs": data.inputs,
            "outputs": {
                "files": data.output_files,
                "metrics": data.output_metrics,
                "summary": data.output_summary,
            },
            "errors": data.errors,
        }
        run.output_summary = summary

        if data.warnings:
            warnings = copy.deepcopy(run.warnings or {"schema_version": 1, "items": []})
            existing = {
                (item.get("message"), item.get("source"))
                for item in warnings.get("items", [])
            }
            for message in data.warnings:
                if (message, "agent_output") in existing:
                    continue
                warnings.setdefault("items", []).append(
                    {
                        "level": "warning",
                        "message": message,
                        "created_at": _iso(_now()),
                        "source": "agent_output",
                    }
                )
            run.warnings = warnings

    return await _mutate(db, agent_run_id, change)


def scratchpad_report(runs: list[AgentRun]) -> dict[str, Any]:
    """The reporter's cross-agent view, assembled from the runs."""
    agents = []
    activity: list[dict[str, Any]] = []
    for run in runs:
        notes = run.durable_notes or {}
        summary = (run.output_summary or {}).get("scratchpad_summary")
        flags = (run.warnings or {}).get("items", [])
        agents.append(
            {
                "agent_id": run.step.agent_type,
                "agent_run_id": str(run.agent_run_id),
                "step_id": str(run.step_id),
                "step_order": run.step.step_order,
                "role": notes.get("role", ""),
                "status": notes.get("status") or _UI_STATUS[run.status].lower(),
                "run_status": run.status,
                "registered_at": notes.get("registered_at"),
                "flag_count": len(flags),
                "flags": flags,
                "has_summary": bool(summary),
                "summary": summary,
            }
        )
        for entry in notes.get("activity_log", []):
            activity.append({**entry, "agent_id": run.step.agent_type, "role": notes.get("role", "")})

    statuses = {entry["status"] for entry in agents}
    if not agents:
        overall = "no_agents"
    elif "failed" in statuses:
        overall = "failed"
    elif "blocked" in statuses:
        overall = "blocked"
    elif statuses - {"done"}:
        overall = "in_progress"
    else:
        overall = "all_done"

    return {
        "overall_status": overall,
        "agent_count": len(agents),
        "agents": agents,
        "activity_log": activity,
    }
