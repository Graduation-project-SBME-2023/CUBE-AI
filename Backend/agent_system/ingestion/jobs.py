"""Durable status records for ingestion jobs.

Converting a large volume takes long enough that the HTTP request must return
immediately, so the uploader polls a job instead.  Records are plain JSON files
under ``Backend/storage/jobs/ingestion``, mirroring how inference jobs are
already stored, and every write is atomic so a poll can never read half a file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from storage_paths import INGEST_JOBS_DIR

logger = logging.getLogger(__name__)

QUEUED = "queued"
CONVERTING = "converting"
UPLOADING = "uploading"
VERIFYING = "verifying"
SYNCING = "syncing"
COMPLETED = "completed"
FAILED = "failed"

TERMINAL_STATUSES = frozenset({COMPLETED, FAILED})

_JOB_ID_ALLOWED = set("0123456789abcdefABCDEF-_")
_DEFAULT_STALE_SECONDS = 3600.0


def _stale_after_seconds() -> float:
    try:
        return max(1.0, float(os.environ.get("INGEST_JOB_STALE_SECONDS", _DEFAULT_STALE_SECONDS)))
    except (TypeError, ValueError):
        return _DEFAULT_STALE_SECONDS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_job_id(job_id: str) -> str:
    value = str(job_id).strip()
    if not value or len(value) > 128 or not set(value) <= _JOB_ID_ALLOWED:
        raise ValueError(f"Invalid ingestion job id: {job_id!r}")
    return value


def job_path(job_id: str) -> Path:
    return INGEST_JOBS_DIR / f"{_safe_job_id(job_id)}.json"


def job_id_for_checksum(checksum: str, modality: str = "", body_part: str = "") -> str:
    """A job id that repeats for the same file, so re-uploads reuse one record.

    The declared modality is part of the id.  Series identity in
    ``nifti_to_dicom`` is scoped by (checksum, modality) for the same reason:
    Orthanc deduplicates instances by SOPInstanceUID and silently keeps
    whichever content it stored first, so the same bytes re-declared under a
    different modality must become a distinct upload rather than colliding
    with -- and losing to -- the earlier one. The declared body part is scoped
    the same way and for the same reason: it reaches the DICOM as
    BodyPartExamined, so correcting it on a re-upload has to start a new job
    rather than be turned away as a duplicate of the first.
    """
    if not checksum:
        raise ValueError("a checksum is required to derive a job id")
    if not modality:
        # Back-compat form, used only where the modality genuinely is not
        # known yet (e.g. looking up a job by checksum alone).
        return checksum[:16]
    scope = f"{checksum}:{modality.strip().upper()}"
    if body_part:
        # Appended only when declared, so every job id minted before body part
        # existed keeps resolving to the same record.
        scope = f"{scope}:{body_part.strip().upper()}"
    digest = hashlib.sha256(scope.encode()).hexdigest()
    return digest[:16]


def write_job(job_id: str, record: dict[str, Any]) -> dict[str, Any]:
    """Replace a job record atomically."""
    path = job_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**record, "job_id": _safe_job_id(job_id), "updated_at": _now()}

    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.stem}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return payload


def read_job(job_id: str) -> dict[str, Any] | None:
    path = job_path(job_id)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read ingestion job %s: %s", job_id, exc)
        return None


def update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    """Merge fields into an existing record, creating it if it vanished."""
    record = read_job(job_id) or {"job_id": job_id, "created_at": _now()}
    record.update(changes)
    return write_job(job_id, record)


def append_warnings(job_id: str, warnings: Iterable[str]) -> None:
    new = [str(warning) for warning in warnings if str(warning).strip()]
    if not new:
        return
    record = read_job(job_id) or {}
    existing = list(record.get("warnings") or [])
    update_job(job_id, warnings=existing + new)


def is_active(record: dict[str, Any] | None, *, stale_after_seconds: float | None = None) -> bool:
    """True when a job is genuinely still running.

    A worker that is killed mid-conversion leaves its record stuck on a
    non-terminal status.  Without a staleness window that record would block
    every future upload of the same file, because job ids are derived from the
    file checksum -- so an abandoned job is treated as retryable.
    """
    if not record or record.get("status") in TERMINAL_STATUSES:
        return False

    updated = record.get("updated_at")
    try:
        last_seen = datetime.fromisoformat(str(updated))
    except (TypeError, ValueError):
        return False
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)

    limit = stale_after_seconds if stale_after_seconds is not None else _stale_after_seconds()
    age = (datetime.now(timezone.utc) - last_seen).total_seconds()
    if age >= limit:
        logger.warning(
            "Ingestion job %s has been %s for %.0fs; treating it as abandoned",
            record.get("job_id"),
            record.get("status"),
            age,
        )
        return False
    return True


def create_job(
    job_id: str,
    *,
    filename: str,
    checksum: str,
    modality: str,
    size_bytes: int,
) -> dict[str, Any]:
    return write_job(
        job_id,
        {
            "job_id": job_id,
            "status": QUEUED,
            "created_at": _now(),
            "filename": filename,
            "checksum": checksum,
            "modality": str(modality).upper(),
            "size_bytes": int(size_bytes),
            "slice_count": 0,
            "uploaded_count": 0,
            "series_instance_uid": None,
            "study_instance_uid": None,
            "patient_id": None,
            "orthanc_series_id": None,
            "warnings": [],
            "error": None,
            "verification": None,
            "provenance": None,
        },
    )


def fail_job(job_id: str, stage: str, message: str) -> dict[str, Any]:
    logger.error("Ingestion job %s failed during %s: %s", job_id, stage, message)
    return update_job(job_id, status=FAILED, stage=stage, error=message)


def prune_finished_jobs(max_age_seconds: float) -> int:
    """Delete terminal job records older than ``max_age_seconds``.

    Job records are status, not study data: once the series is in Orthanc and
    the database, the record has served its purpose.
    """
    if max_age_seconds <= 0 or not INGEST_JOBS_DIR.is_dir():
        return 0

    cutoff = time.time() - max_age_seconds
    removed = 0
    for path in INGEST_JOBS_DIR.glob("*.json"):
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            with open(path, "r", encoding="utf-8") as handle:
                status = json.load(handle).get("status")
            if status in TERMINAL_STATUSES:
                path.unlink(missing_ok=True)
                removed += 1
        except (OSError, json.JSONDecodeError):
            continue
    return removed
