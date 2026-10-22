"""The Population and Segmentation Review UI, backed by PostgreSQL."""

from __future__ import annotations

import csv
import io
import logging
import os
from typing import Any

import httpx

from services.timeout_policy import CONTROL_HTTP_TIMEOUT

logger = logging.getLogger(__name__)

POPULATION_AGENT_TYPE = "study_population_planner_agent"
POPULATION_COHORT_KEY = "population"


class DomainError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def _base_url() -> str:
    return os.environ.get(
        "APP_API_URL", os.environ.get("CONTROL_API_URL", "http://localhost:8001")
    ).rstrip("/")


async def _api(method: str, path: str, *, allow_404: bool = False, **kwargs) -> Any:
    async with httpx.AsyncClient(timeout=CONTROL_HTTP_TIMEOUT) as client:
        response = await client.request(method, f"{_base_url()}{path}", **kwargs)
    if allow_404 and response.status_code == 404:
        return None
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        raise DomainError(response.status_code, detail)
    return response.json() if response.content else None


# ----------------------------------------------------------------------
# Cohort
# ----------------------------------------------------------------------
async def population_run_id(session_id: str) -> str:
    resolved = await _api(
        "GET", f"/runs/session/{session_id}/by-target/{POPULATION_AGENT_TYPE}"
    )
    return str(resolved["run"]["run_id"])


async def get_cohort(session_id: str) -> dict[str, Any] | None:
    try:
        return await _api("GET", f"/cohorts/session/{session_id}/population", allow_404=True)
    except DomainError as exc:
        if exc.status_code == 409:
            # No approved population step yet: nothing to show, not an error.
            return None
        raise


async def ensure_cohort(session_id: str, **documents: Any) -> dict[str, Any]:
    run_id = await population_run_id(session_id)
    return await _api(
        "PUT", f"/cohorts/run/{run_id}/{POPULATION_COHORT_KEY}", json=documents
    )


def _field(cohort: dict[str, Any]) -> str:
    return (cohort.get("label_definition") or {}).get("field", "has_target_disease")


def label_records(cohort: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Members in the UI's label-record shape."""
    if not cohort:
        return []
    field = _field(cohort)
    records = []
    for member in sorted(cohort.get("members") or [], key=lambda item: item["ordinal"]):
        value = (member.get("label") or {}).get(field)
        provenance = member.get("label_provenance") or {}
        confidence = (
            "manual override"
            if member.get("label_source") == "manual_override"
            else member.get("label_confidence") or "unlabeled"
        )
        records.append(
            {
                "patient_id": member["volume_series_instance_uid"],
                "subject_key": member["subject_key"],
                "has_target_disease": "" if value is None else ("true" if value else "false"),
                "confidence": confidence,
                "label_source": member.get("label_source"),
                "requires_manual_review": bool(
                    provenance.get("requires_manual_review", value is None)
                ),
                "source_evidence": provenance.get("source_evidence", ""),
            }
        )
    return records


def cohort_state(cohort: dict[str, Any] | None) -> dict[str, Any]:
    """The `/populations/cohort-state` response, from the cohort row."""
    if not cohort:
        return {
            "datasetlist": [],
            "labelling_mode": "automatic",
            "cohort_approved": False,
            "inclusion_criteria": [],
            "exclusion_criteria": [],
            "labels": [],
        }
    criteria = cohort.get("criteria") or {}
    members = sorted(cohort.get("members") or [], key=lambda item: item["ordinal"])
    return {
        "cohort_id": cohort["cohort_id"],
        "datasetlist": [member["volume_series_instance_uid"] for member in members],
        "labelling_mode": criteria.get("labelling_mode", "automatic"),
        "cohort_approved": bool(cohort.get("frozen_at")),
        "frozen_at": cohort.get("frozen_at"),
        "inclusion_criteria": criteria.get("inclusion_criteria", []),
        "exclusion_criteria": criteria.get("exclusion_criteria", []),
        "modality": criteria.get("modality"),
        "anatomy_validated": bool(
            ((cohort.get("selection_summary") or {}).get("anatomy_validation") or {}).get(
                "anatomy_validated"
            )
        ),
        "labels": label_records(cohort),
    }


def _parse_value(raw: Any) -> bool | None:
    text = str(raw if raw is not None else "").strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def manual_label_writes(cohort: dict[str, Any], ui_labels: list[dict[str, Any]]) -> list[dict]:
    """Translate a Patient Labeling save into label writes."""
    field = _field(cohort)
    members = {
        member["volume_series_instance_uid"]: member for member in cohort.get("members") or []
    }
    writes = []
    for record in ui_labels:
        uid = str(record.get("patient_id") or "")
        member = members.get(uid)
        if member is None:
            continue
        new_value = _parse_value(record.get("has_target_disease"))
        current = (member.get("label") or {}).get(field)
        manual = str(record.get("confidence") or "").strip().lower() == "manual override"
        if new_value == current and not manual:
            continue
        if new_value == current and manual and member.get("label_source") == "manual_override":
            continue
        writes.append(
            {
                "volume_series_instance_uid": uid,
                "has_target_disease": new_value,
                "label_source": "manual_override",
                "label_confidence": None,
                "label_provenance": {
                    "schema_version": 1,
                    "requires_manual_review": new_value is None,
                    "source_evidence": str(record.get("source_evidence") or "Manually assigned in UI")[:20000],
                    "previous_source": member.get("label_source"),
                },
            }
        )
    return writes


def uploaded_target_writes(cohort: dict[str, Any], csv_bytes: bytes) -> tuple[list[dict], list[str]]:
    """A doctor-uploaded targets file becomes `uploaded_targets` labels."""
    text = csv_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise DomainError(422, "The uploaded targets file has no header row")
    columns = {name.strip().lower(): name for name in reader.fieldnames}
    id_column = columns.get("id") or columns.get("volume_series_instance_uid") or reader.fieldnames[0]
    target_column = columns.get("target") or columns.get("label") or reader.fieldnames[-1]
    members = {
        member["volume_series_instance_uid"] for member in cohort.get("members") or []
    }
    writes, unmatched = [], []
    for row in reader:
        uid = str(row.get(id_column) or "").strip()
        if uid not in members:
            if uid:
                unmatched.append(uid)
            continue
        value = _parse_value(row.get(target_column))
        writes.append(
            {
                "volume_series_instance_uid": uid,
                "has_target_disease": value,
                "label_source": "uploaded_targets" if value is not None else "manual_override",
                "label_confidence": None,
                "label_provenance": {
                    "schema_version": 1,
                    "requires_manual_review": value is None,
                    "source_evidence": "Uploaded targets file",
                },
            }
        )
    return writes, unmatched


# ----------------------------------------------------------------------
# Segmentation review
# ----------------------------------------------------------------------
async def current_batch(session_id: str) -> dict[str, Any] | None:
    """The session's segmentation batch the review UI is about (the last one in
    plan order)."""
    batches = await _api("GET", f"/segmentation-batches/session/{session_id}")
    return batches[-1] if batches else None


def review_payload(batch: dict[str, Any]) -> dict[str, list[str]]:
    decisions: dict[str, list[str]] = {"accepted": [], "rejected": [], "modified": []}
    for pair in batch.get("pairs") or []:
        decision = pair.get("review_decision")
        if decision in decisions:
            decisions[decision].append(pair["pair_key"])
    return decisions


def snapshot_manifest(batch: dict[str, Any]) -> dict[str, Any]:
    """The manifest-shaped view `build_snapshot_from_manifest` renders.

    Reconstructed from the rows on read; nothing writes it.
    """
    from agents.execution_agents.segmentation.batch_store import work_items_from_batch

    stage = {
        "not_ready": "processing",
        "pending": "awaiting_review",
        "submitting": "awaiting_review",
        "submitted": "reviewed",
        "auto_accepted": "reviewed",
    }[batch["review_status"]]
    config = batch.get("config") or {}
    return {
        "batch_correlation_id": config.get("batch_correlation_id"),
        "stage": stage,
        "resolution": {"final_plan": config.get("final_plan")},
        "pairs": work_items_from_batch(batch),
    }
