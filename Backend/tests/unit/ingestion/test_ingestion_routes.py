"""Route-level validation, with the background conversion stubbed out.

The rules worth pinning down here are the ones that protect the data: modality
must be stated rather than guessed, unknown file types never reach the
converter, and an oversized upload is refused before it fills the disk.
"""

from __future__ import annotations

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ingestion import jobs, routes


@pytest.fixture(autouse=True)
def isolated_storage():
    """Job ids come from the file checksum, so identical payloads collide.

    Every test in this module uploads the same bytes on purpose; clearing the
    shared job and staging directories keeps them from leaking into each other.
    """
    for directory in (jobs.INGEST_JOBS_DIR, routes.TEMP_DIR / "ingest"):
        if directory.is_dir():
            for item in directory.iterdir():
                item.unlink(missing_ok=True)
    yield


@pytest.fixture
def client(monkeypatch):
    calls: list[dict] = []

    async def _record(job_id, **kwargs):
        calls.append({"job_id": job_id, **kwargs})
        return {}

    monkeypatch.setattr(routes, "run_ingestion", _record)

    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as test_client:
        test_client.recorded_calls = calls
        yield test_client


def _upload(name: str = "scan.nii.gz", payload: bytes = b"volume-bytes"):
    return {"file": (name, io.BytesIO(payload), "application/octet-stream")}


@pytest.mark.unit
def test_upload_queues_a_job_and_returns_its_id(client):
    response = client.post("/ingest/nifti", files=_upload(), data={"modality": "CT"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == jobs.QUEUED
    assert body["duplicate"] is False
    assert len(client.recorded_calls) == 1
    assert client.recorded_calls[0]["modality"] == "CT"


@pytest.mark.unit
def test_modality_is_required(client):
    response = client.post("/ingest/nifti", files=_upload())

    assert response.status_code == 422
    assert client.recorded_calls == []


@pytest.mark.unit
def test_unknown_modality_is_refused_rather_than_defaulted(client):
    response = client.post(
        "/ingest/nifti", files=_upload(), data={"modality": "ULTRASOUND"}
    )

    assert response.status_code == 400
    assert "Unsupported modality" in response.json()["detail"]
    assert client.recorded_calls == []


@pytest.mark.unit
def test_modality_is_normalised_to_upper_case(client):
    client.post("/ingest/nifti", files=_upload(), data={"modality": "  mr "})

    assert client.recorded_calls[0]["modality"] == "MR"


@pytest.mark.unit
def test_unsupported_extension_is_refused(client):
    response = client.post(
        "/ingest/nifti", files=_upload(name="scan.dcm"), data={"modality": "CT"}
    )

    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]
    assert client.recorded_calls == []


@pytest.mark.unit
def test_empty_file_is_refused(client):
    response = client.post(
        "/ingest/nifti", files=_upload(payload=b""), data={"modality": "CT"}
    )

    assert response.status_code == 400
    assert client.recorded_calls == []


@pytest.mark.unit
def test_oversized_upload_is_refused_and_leaves_no_file(client, monkeypatch):
    monkeypatch.setenv("INGEST_MAX_UPLOAD_BYTES", "8")
    staging = routes.TEMP_DIR / "ingest"
    staging.mkdir(parents=True, exist_ok=True)
    before = set(staging.iterdir())

    response = client.post(
        "/ingest/nifti", files=_upload(payload=b"x" * 64), data={"modality": "CT"}
    )

    assert response.status_code == 413
    assert client.recorded_calls == []
    # The partial write must be removed, not left behind to fill the disk.
    assert set(staging.iterdir()) == before


@pytest.mark.unit
def test_reuploading_a_running_file_does_not_start_a_second_job(client):
    first = client.post("/ingest/nifti", files=_upload(), data={"modality": "CT"})
    job_id = first.json()["job_id"]
    jobs.update_job(job_id, status=jobs.CONVERTING)

    second = client.post("/ingest/nifti", files=_upload(), data={"modality": "CT"})

    assert second.json() == {
        "job_id": job_id,
        "status": jobs.CONVERTING,
        "duplicate": True,
    }
    assert len(client.recorded_calls) == 1


@pytest.mark.unit
def test_an_abandoned_job_does_not_block_the_file_forever(client, monkeypatch):
    # A worker killed mid-conversion leaves a non-terminal record. Without a
    # staleness window that file could never be uploaded again, because the job
    # id is derived from its checksum.
    first = client.post("/ingest/nifti", files=_upload(), data={"modality": "CT"})
    jobs.update_job(first.json()["job_id"], status=jobs.CONVERTING)
    monkeypatch.setenv("INGEST_JOB_STALE_SECONDS", "1")
    import time

    time.sleep(1.1)

    second = client.post("/ingest/nifti", files=_upload(), data={"modality": "CT"})

    assert second.json()["duplicate"] is False
    assert len(client.recorded_calls) == 2


@pytest.mark.unit
def test_a_finished_job_can_be_retried(client):
    first = client.post("/ingest/nifti", files=_upload(), data={"modality": "CT"})
    job_id = first.json()["job_id"]
    jobs.update_job(job_id, status=jobs.FAILED)

    second = client.post("/ingest/nifti", files=_upload(), data={"modality": "CT"})

    assert second.json()["duplicate"] is False
    assert len(client.recorded_calls) == 2


@pytest.mark.unit
def test_optional_placement_fields_reach_the_worker(client):
    client.post(
        "/ingest/nifti",
        files=_upload(),
        data={
            "modality": "CT",
            "study_uid": "1.2.3.4",
            "patient_id": "REAL-1",
            "patient_name": "DOE^JANE",
        },
    )
    call = client.recorded_calls[0]

    assert call["study_instance_uid"] == "1.2.3.4"
    assert call["patient_id"] == "REAL-1"
    assert call["patient_name"] == "DOE^JANE"


@pytest.mark.unit
def test_blank_optional_fields_become_none(client):
    client.post(
        "/ingest/nifti",
        files=_upload(),
        data={"modality": "CT", "study_uid": "", "patient_id": ""},
    )
    call = client.recorded_calls[0]

    assert call["study_instance_uid"] is None
    assert call["patient_id"] is None


@pytest.mark.unit
def test_job_status_is_readable_and_missing_jobs_are_404(client):
    created = client.post(
        "/ingest/nifti", files=_upload(), data={"modality": "CT"}
    ).json()

    found = client.get(f"/ingest/jobs/{created['job_id']}")
    missing = client.get("/ingest/jobs/0000000000000000")

    assert found.status_code == 200
    assert found.json()["job_id"] == created["job_id"]
    assert missing.status_code == 404


@pytest.mark.unit
def test_a_traversal_job_id_is_rejected(client):
    assert client.get("/ingest/jobs/..%2F..%2Fetc").status_code in (400, 404)


@pytest.mark.unit
def test_formats_endpoint_advertises_what_the_ui_needs(client):
    body = client.get("/ingest/formats").json()

    assert ".nii.gz" in body["extensions"]
    assert ".nrrd" in body["extensions"]
    assert set(body["modalities"]) >= {"CT", "MR", "OT"}
    assert body["max_upload_bytes"] > 0
