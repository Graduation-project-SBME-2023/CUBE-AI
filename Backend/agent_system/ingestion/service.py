"""Run an ingestion job: convert, store in Orthanc, verify, register.

The conversion itself lives in :mod:`ingestion.nifti_to_dicom` and stays pure.
This module owns the parts that touch the outside world -- Orthanc, the
Application API sync hook, the CPU lease and the job record -- so the two can be
tested independently.

The agent system never writes to PostgreSQL.  A stored series reaches the
database exactly the way a DICOM SEG already does: Orthanc's Lua hook posts to
``/sync/orthanc-webhook``, and a direct call is made as well because that hook
can be missed while MONAI is busy.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from clients.orthanc_client import OrthancClient, OrthancError
from ingestion import jobs
from ingestion.nifti_to_dicom import (
    SeriesIdentity,
    convert_volume_to_dicom,
    synthetic_series_identity,
)
from ingestion.provenance import (
    SEGMENTABLE_MODALITIES,
    normalise_extension,
    provenance_summary,
)
from ingestion.verify import verify_round_trip

logger = logging.getLogger(__name__)

SYNC_TIMEOUT_SECONDS = 30.0


@asynccontextmanager
async def _maybe_lease(
    scheduler: Any | None,
    resources: list[str],
    **kwargs: Any,
) -> AsyncIterator[None]:
    """Take a resource lease when a scheduler is given; a no-op otherwise.

    Lets a bare call to ``run_ingestion`` (as every unit test makes) skip
    capacity limiting entirely, while production code -- which always passes
    ``ingestion_limiter`` -- gets it for free.
    """
    if scheduler is None:
        yield None
        return
    async with scheduler.lease(resources, **kwargs):
        yield None


def _app_api_url() -> str:
    return os.environ.get(
        "APP_API_URL",
        os.environ.get("CONTROL_API_URL", "http://localhost:8001"),
    ).rstrip("/")


async def resolve_identity(
    orthanc: OrthancClient,
    *,
    checksum: str,
    study_instance_uid: str | None,
    patient_id: str | None,
    patient_name: str | None,
    created_at: datetime | None = None,
) -> SeriesIdentity:
    """Decide who the new series belongs to.

    With a ``study_instance_uid`` the series joins that study and inherits its
    patient, which is what makes an imported volume comparable with the scans
    already on file.  Without one, a stable pseudonymous identity is derived from
    the file checksum so re-uploading the same file cannot create a second
    patient.
    """
    identity = synthetic_series_identity(checksum, created_at=created_at)

    if study_instance_uid:
        orthanc_study_id = await orthanc.find_study(study_instance_uid)
        if not orthanc_study_id:
            raise OrthancError(
                f"Study {study_instance_uid} was not found in Orthanc; "
                "upload the study first or omit study_uid"
            )
        info = await orthanc.study_info(orthanc_study_id)
        study_tags = info.get("MainDicomTags") or {}
        patient_tags = info.get("PatientMainDicomTags") or {}
        identity = SeriesIdentity(
            patient_id=patient_tags.get("PatientID") or identity.patient_id,
            patient_name=patient_tags.get("PatientName") or identity.patient_name,
            patient_birth_date=patient_tags.get("PatientBirthDate") or "",
            patient_sex=patient_tags.get("PatientSex") or "",
            study_instance_uid=study_instance_uid,
            study_date=study_tags.get("StudyDate") or "",
            study_time=study_tags.get("StudyTime") or "",
            accession_number=study_tags.get("AccessionNumber") or "",
            study_description=study_tags.get("StudyDescription") or "Volume Import",
            study_id=study_tags.get("StudyID") or "",
        )

    # Explicit values always win over both the inherited and synthetic identity.
    if patient_id or patient_name:
        identity = SeriesIdentity(
            patient_id=patient_id or identity.patient_id,
            patient_name=patient_name or identity.patient_name,
            patient_birth_date=identity.patient_birth_date,
            patient_sex=identity.patient_sex,
            study_instance_uid=identity.study_instance_uid,
            study_date=identity.study_date,
            study_time=identity.study_time,
            accession_number=identity.accession_number,
            study_description=identity.study_description,
            study_id=identity.study_id,
        )
    return identity


async def sync_series_to_database(
    orthanc_series_id: str,
    series_instance_uid: str,
    modality: str,
) -> bool:
    """Ask the Application API to register the series now.

    Orthanc's own Lua hook fires only once the series goes stable (30 seconds by
    default) and can be dropped entirely if the receiver is busy, which would
    leave a freshly uploaded study invisible in the cohort browser.
    """
    url = f"{_app_api_url()}/sync/orthanc-webhook"
    payload = {
        "change_type": "stable_series",
        "resource_id": orthanc_series_id,
        "level": "series",
        "modality": str(modality).upper(),
        "series_uid": series_instance_uid,
    }
    try:
        async with httpx.AsyncClient(timeout=SYNC_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload)
        if 200 <= response.status_code < 300:
            return True
        logger.warning(
            "Direct sync for ingested series %s returned HTTP %s: %s",
            series_instance_uid,
            response.status_code,
            response.text[:300],
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "Direct sync for ingested series %s failed: %s", series_instance_uid, exc
        )
    return False


async def run_ingestion(
    job_id: str,
    *,
    source_path: Path,
    filename: str,
    checksum: str,
    modality: str,
    study_instance_uid: str | None = None,
    patient_id: str | None = None,
    patient_name: str | None = None,
    series_description: str | None = None,
    orthanc: OrthancClient | None = None,
    scheduler: Any | None = None,
) -> dict[str, Any]:
    """Convert one uploaded volume and store it, updating the job as it goes."""
    client = orthanc or OrthancClient()
    owns_client = orthanc is None
    uploaded_series_id: str | None = None
    # Known as soon as conversion succeeds -- well before the upload loop
    # finishes -- so a failure partway through uploading still has an address
    # to clean up by, instead of only being able to clean up a series whose
    # upload loop ran to completion.
    series_instance_uid: str | None = None

    try:
        identity = await resolve_identity(
            client,
            checksum=checksum,
            study_instance_uid=study_instance_uid,
            patient_id=patient_id,
            patient_name=patient_name,
        )

        jobs.update_job(job_id, status=jobs.CONVERTING, stage="conversion")
        conversion = await _convert(
            source_path,
            modality=modality,
            identity=identity,
            checksum=checksum,
            filename=filename,
            series_description=series_description,
            scheduler=scheduler,
        )

        series_instance_uid = conversion.series_instance_uid

        warnings = list(conversion.warnings)
        if str(modality).upper() not in SEGMENTABLE_MODALITIES:
            warnings.append(
                f"Modality {str(modality).upper()} is stored as Secondary Capture, "
                "which has no image-plane geometry; segmentation may not be able to "
                "use this series"
            )

        jobs.update_job(
            job_id,
            status=jobs.UPLOADING,
            stage="upload",
            slice_count=len(conversion.datasets),
            series_instance_uid=conversion.series_instance_uid,
            study_instance_uid=identity.study_instance_uid,
            patient_id=identity.patient_id,
            warnings=warnings,
            provenance=provenance_summary(
                checksum=checksum,
                source_filename=filename,
                # Path.suffix only returns the last segment ("gz" for
                # "scan.nii.gz"); the job record should show the real
                # container extension, not a truncated one.
                source_extension=normalise_extension(filename),
                declared_modality=modality,
                series_instance_uid=conversion.series_instance_uid,
                study_instance_uid=identity.study_instance_uid,
                patient_id=identity.patient_id,
            ),
        )

        # Uploading is hundreds of sequential requests for a real volume, and
        # several large uploads running this loop at once is exactly what was
        # saturating Orthanc's HTTP server and producing dropped connections.
        # This is the same capacity-limiting the rest of the pipeline already
        # applies to expensive stages -- see ingestion/limits.py.
        async with _maybe_lease(
            scheduler,
            ["orthanc_io"],
            session_id=f"ingest-{checksum[:16]}",
            operation="volume_upload",
            agent_id="ingestion",
        ):
            for index, dataset in enumerate(conversion.datasets, start=1):
                await client.upload_dataset(dataset)
                if index % 25 == 0 or index == len(conversion.datasets):
                    jobs.update_job(job_id, uploaded_count=index)

            uploaded_series_id = await client.find_series(conversion.series_instance_uid)
            if not uploaded_series_id:
                raise OrthancError(
                    "Orthanc accepted every instance but the series cannot be found"
                )

            jobs.update_job(job_id, status=jobs.VERIFYING, stage="verification",
                            orthanc_series_id=uploaded_series_id)
            verification = await verify_round_trip(
                client,
                series_instance_uid=conversion.series_instance_uid,
                source_path=source_path,
                rescale_slope=conversion.rescale_slope,
            )
        if not verification["passed"]:
            # A series that does not match its source is worse than no series at
            # all, because nothing downstream would ever question it.
            await client.delete_series(uploaded_series_id)
            uploaded_series_id = None
            jobs.update_job(job_id, verification=verification)
            return jobs.fail_job(
                job_id,
                "verification",
                "The stored series did not match the uploaded volume ("
                + ", ".join(verification["failures"])
                + "); it was removed from Orthanc",
            )

        jobs.update_job(job_id, status=jobs.SYNCING, stage="sync",
                        verification=verification)
        synced = await sync_series_to_database(
            uploaded_series_id,
            conversion.series_instance_uid,
            modality,
        )
        if not synced:
            warnings.append(
                "The series is stored in Orthanc but the database sync call did not "
                "succeed; Orthanc's own hook should register it shortly"
            )

        return jobs.update_job(
            job_id,
            status=jobs.COMPLETED,
            stage="completed",
            synced=synced,
            warnings=warnings,
            error=None,
        )

    except Exception as exc:  # noqa: BLE001 - the job record is the error channel
        logger.exception("Ingestion job %s failed", job_id)
        # Never leave a half-written series behind for someone to analyse.
        # uploaded_series_id is only set once every slice has landed and
        # find_series has confirmed it; a failure inside the upload loop
        # itself -- the exact case a dropped connection produces -- leaves it
        # None even though some slices did reach Orthanc.  Fall back to
        # looking the series up by the UID the conversion already fixed, so a
        # partial series still gets cleaned up instead of sitting there for
        # Orthanc's own stable-series hook to register as if it were whole.
        cleanup_id = uploaded_series_id
        if not cleanup_id and series_instance_uid:
            try:
                cleanup_id = await client.find_series(series_instance_uid)
            except Exception:  # noqa: BLE001 - best-effort cleanup lookup
                cleanup_id = None
        if cleanup_id:
            await client.delete_series(cleanup_id)
        return jobs.fail_job(job_id, "ingestion", str(exc))
    finally:
        Path(source_path).unlink(missing_ok=True)
        if owns_client:
            await client.close()


async def _convert(
    source_path: Path,
    *,
    modality: str,
    identity: SeriesIdentity,
    checksum: str,
    filename: str,
    series_description: str | None,
    scheduler: Any | None,
):
    """Run the CPU-bound conversion off the event loop, under a lease if given."""

    def _work():
        return convert_volume_to_dicom(
            source_path,
            modality=modality,
            identity=identity,
            checksum=checksum,
            source_filename=filename,
            series_description=series_description,
        )

    if scheduler is None:
        return await asyncio.to_thread(_work)

    async with scheduler.lease(
        ["cpu_heavy"],
        session_id=f"ingest-{checksum[:16]}",
        operation="volume_ingestion",
        agent_id="ingestion",
    ):
        return await asyncio.to_thread(_work)
