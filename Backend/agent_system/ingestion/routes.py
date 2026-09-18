"""HTTP surface for volume ingestion.

Kept in its own router rather than added to ``app.py``: that module is already
2,000+ lines, and a self-contained feature has no reason to grow it further.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from ingestion import jobs
from ingestion.provenance import (
    SOP_CLASS_BY_MODALITY,
    SUPPORTED_EXTENSIONS,
    normalise_extension,
)
from ingestion.service import run_ingestion
from services.resource_scheduler import resource_scheduler
from storage_paths import TEMP_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingestion"])

_UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024
_DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB


def _max_upload_bytes() -> int:
    try:
        return max(1, int(os.environ.get("INGEST_MAX_UPLOAD_BYTES", _DEFAULT_MAX_BYTES)))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_BYTES


def _staging_path(extension: str) -> Path:
    directory = TEMP_DIR / "ingest"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{uuid.uuid4().hex}{extension}"


async def _stream_to_disk(upload: UploadFile, destination: Path) -> tuple[str, int]:
    """Write the upload to disk in chunks, hashing as it goes.

    A CT volume can be hundreds of megabytes; it must never be held in memory,
    and reading it twice just to hash it would double the I/O.
    """
    digest = hashlib.sha256()
    size = 0
    limit = _max_upload_bytes()

    try:
        with open(destination, "wb") as handle:
            while chunk := await upload.read(_UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the {limit} byte ingestion limit",
                    )
                digest.update(chunk)
                handle.write(chunk)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise

    if size == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    return digest.hexdigest(), size


@router.get("/formats")
async def supported_formats():
    """What the uploader may send, and what it must declare."""
    return {
        "extensions": list(SUPPORTED_EXTENSIONS),
        "modalities": sorted(SOP_CLASS_BY_MODALITY),
        "max_upload_bytes": _max_upload_bytes(),
    }


@router.post("/nifti")
async def ingest_volume(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    modality: str = Form(...),
    study_uid: str | None = Form(None),
    patient_id: str | None = Form(None),
    patient_name: str | None = Form(None),
    series_description: str | None = Form(None),
):
    """Accept a volume file and convert it into a DICOM series in the background.

    ``modality`` is required on purpose.  Cohort queries filter on it, so a
    guessed value would quietly hide the series -- and calling something CT when
    its units are unknown is a scientific error, not a formatting one.
    """
    filename = file.filename or "upload"
    try:
        extension = normalise_extension(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if str(modality).strip().upper() not in SOP_CLASS_BY_MODALITY:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported modality {modality!r}; expected one of "
                f"{', '.join(sorted(SOP_CLASS_BY_MODALITY))}"
            ),
        )
    modality = str(modality).strip().upper()

    destination = _staging_path(extension)
    checksum, size = await _stream_to_disk(file, destination)
    # Modality is part of the job id, not just the checksum: the same bytes
    # re-declared under a different modality produce a genuinely different
    # DICOM series (see nifti_to_dicom.convert_volume_to_dicom), so they need
    # their own job record rather than overwriting the first upload's.
    job_id = jobs.job_id_for_checksum(checksum, modality)

    existing = jobs.read_job(job_id)
    if jobs.is_active(existing):
        # The same file is already being converted; do not start a second run.
        # An abandoned job is not "active", so a crashed conversion can be retried.
        destination.unlink(missing_ok=True)
        return {"job_id": job_id, "status": existing.get("status"), "duplicate": True}

    record = jobs.create_job(
        job_id,
        filename=filename,
        checksum=checksum,
        modality=modality,
        size_bytes=size,
    )
    background.add_task(
        run_ingestion,
        job_id,
        source_path=destination,
        filename=filename,
        checksum=checksum,
        modality=modality,
        study_instance_uid=(study_uid or None),
        patient_id=(patient_id or None),
        patient_name=(patient_name or None),
        series_description=(series_description or None),
        # Share the pipeline's CPU lease so a conversion cannot compete with a
        # radiomics run for the same cores.
        scheduler=resource_scheduler,
    )
    logger.info(
        "Queued ingestion job %s for %s (%s bytes, modality=%s)",
        job_id,
        filename,
        size,
        modality,
    )
    return {"job_id": job_id, "status": record["status"], "duplicate": False}


@router.get("/jobs/{job_id}")
async def ingestion_job_status(job_id: str):
    try:
        record = jobs.read_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    return record
