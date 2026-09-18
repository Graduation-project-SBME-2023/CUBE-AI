"""Whole-job behaviour against a fake Orthanc -- no containers, no network."""

from __future__ import annotations

import numpy as np
import pytest
import SimpleITK as sitk

from ingestion import jobs, service
from ingestion.nifti_to_dicom import SeriesIdentity, file_checksum
from ingestion.provenance import is_ingested_uid

SPACING_XYZ = (0.8, 1.1, 3.0)
ORIGIN_LPS = (-95.5, -60.25, 12.0)


class FakeOrthanc:
    """Stores datasets in memory and answers the handful of calls we make."""

    def __init__(self, *, studies: dict | None = None):
        self.datasets: list = []
        self.studies = studies or {}
        self.deleted: list[str] = []
        self.closed = False

    async def upload_dataset(self, dataset):
        self.datasets.append(dataset)
        return f"instance-{len(self.datasets)}"

    async def find_series(self, series_instance_uid):
        matching = [
            ds for ds in self.datasets if ds.SeriesInstanceUID == series_instance_uid
        ]
        return "orthanc-series-1" if matching else None

    async def read_series_datasets(self, series_instance_uid, stop_before_pixels=False):
        return [
            ds for ds in self.datasets if ds.SeriesInstanceUID == series_instance_uid
        ]

    async def find_study(self, study_instance_uid):
        return self.studies.get(study_instance_uid, {}).get("orthanc_id")

    async def study_info(self, orthanc_study_id):
        for study in self.studies.values():
            if study.get("orthanc_id") == orthanc_study_id:
                return study["info"]
        raise AssertionError(f"unknown study {orthanc_study_id}")

    async def delete_series(self, orthanc_series_id):
        self.deleted.append(orthanc_series_id)
        self.datasets = []
        return True

    async def close(self):
        self.closed = True


def _write_volume(tmp_path, array=None, name="scan.nii.gz"):
    if array is None:
        array = (
            np.linspace(-500, 900, 3 * 4 * 5).reshape((3, 4, 5)).round().astype(np.int16)
        )
    image = sitk.GetImageFromArray(np.ascontiguousarray(array))
    image.SetSpacing(SPACING_XYZ)
    image.SetOrigin(ORIGIN_LPS)
    image.SetDirection((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
    path = tmp_path / name
    sitk.WriteImage(image, str(path))
    return path


@pytest.fixture
def no_sync(monkeypatch):
    calls: list[tuple] = []

    async def _sync(orthanc_series_id, series_instance_uid, modality):
        calls.append((orthanc_series_id, series_instance_uid, modality))
        return True

    monkeypatch.setattr(service, "sync_series_to_database", _sync)
    return calls


def _start_job(path, modality="CT"):
    checksum = file_checksum(path)
    job_id = jobs.job_id_for_checksum(checksum)
    jobs.create_job(
        job_id,
        filename=path.name,
        checksum=checksum,
        modality=modality,
        size_bytes=path.stat().st_size,
    )
    return job_id, checksum


@pytest.mark.unit
@pytest.mark.asyncio
async def test_successful_ingestion_uploads_verifies_and_syncs(tmp_path, no_sync):
    path = _write_volume(tmp_path)
    job_id, checksum = _start_job(path)
    orthanc = FakeOrthanc()

    record = await service.run_ingestion(
        job_id,
        source_path=path,
        filename=path.name,
        checksum=checksum,
        modality="CT",
        orthanc=orthanc,
    )

    assert record["status"] == jobs.COMPLETED
    assert record["slice_count"] == 3
    assert record["uploaded_count"] == 3
    assert record["verification"]["passed"] is True
    assert is_ingested_uid(record["series_instance_uid"])
    assert len(no_sync) == 1
    # The staged upload must not survive the job.
    assert not path.exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_verification_failure_removes_the_series(tmp_path, no_sync, monkeypatch):
    path = _write_volume(tmp_path)
    job_id, checksum = _start_job(path)
    orthanc = FakeOrthanc()

    async def _failing_verify(*_args, **_kwargs):
        return {"passed": False, "failures": ["origin", "voxels"]}

    monkeypatch.setattr(service, "verify_round_trip", _failing_verify)

    record = await service.run_ingestion(
        job_id,
        source_path=path,
        filename=path.name,
        checksum=checksum,
        modality="CT",
        orthanc=orthanc,
    )

    # A series that does not match its source must never stay in the archive.
    assert record["status"] == jobs.FAILED
    assert orthanc.deleted == ["orthanc-series-1"]
    assert "origin" in record["error"]
    assert no_sync == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_unknown_study_fails_before_anything_is_uploaded(tmp_path, no_sync):
    path = _write_volume(tmp_path)
    job_id, checksum = _start_job(path)
    orthanc = FakeOrthanc()

    record = await service.run_ingestion(
        job_id,
        source_path=path,
        filename=path.name,
        checksum=checksum,
        modality="CT",
        study_instance_uid="1.2.3.not.here",
        orthanc=orthanc,
    )

    assert record["status"] == jobs.FAILED
    assert "was not found in Orthanc" in record["error"]
    assert orthanc.datasets == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ot_upload_warns_that_segmentation_may_not_work(tmp_path, no_sync):
    path = _write_volume(tmp_path)
    job_id, checksum = _start_job(path, modality="OT")
    orthanc = FakeOrthanc()

    record = await service.run_ingestion(
        job_id,
        source_path=path,
        filename=path.name,
        checksum=checksum,
        modality="OT",
        orthanc=orthanc,
    )

    assert record["status"] == jobs.COMPLETED
    assert any("segmentation may not" in warning for warning in record["warnings"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_failed_sync_warns_but_keeps_the_series(tmp_path, monkeypatch):
    path = _write_volume(tmp_path)
    job_id, checksum = _start_job(path)
    orthanc = FakeOrthanc()

    async def _sync_fails(*_args, **_kwargs):
        return False

    monkeypatch.setattr(service, "sync_series_to_database", _sync_fails)

    record = await service.run_ingestion(
        job_id,
        source_path=path,
        filename=path.name,
        checksum=checksum,
        modality="CT",
        orthanc=orthanc,
    )

    # Orthanc's own hook still registers it later, so this is a warning not a
    # failure -- the image is safely archived either way.
    assert record["status"] == jobs.COMPLETED
    assert record["synced"] is False
    assert any("database sync" in warning for warning in record["warnings"])
    assert orthanc.deleted == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_identity_is_inherited_from_an_existing_study(tmp_path, no_sync):
    path = _write_volume(tmp_path)
    job_id, checksum = _start_job(path)
    orthanc = FakeOrthanc(
        studies={
            "1.2.840.113619.2.55.3.999": {
                "orthanc_id": "study-1",
                "info": {
                    "MainDicomTags": {
                        "StudyDate": "20240115",
                        "StudyDescription": "Abdomen CT",
                        "AccessionNumber": "ACC-9",
                    },
                    "PatientMainDicomTags": {
                        "PatientID": "REAL-123",
                        "PatientName": "DOE^JANE",
                        "PatientBirthDate": "19700101",
                        "PatientSex": "F",
                    },
                },
            }
        }
    )

    record = await service.run_ingestion(
        job_id,
        source_path=path,
        filename=path.name,
        checksum=checksum,
        modality="CT",
        study_instance_uid="1.2.840.113619.2.55.3.999",
        orthanc=orthanc,
    )

    assert record["status"] == jobs.COMPLETED
    assert record["patient_id"] == "REAL-123"
    first = orthanc.datasets[0]
    assert first.StudyInstanceUID == "1.2.840.113619.2.55.3.999"
    assert first.AccessionNumber == "ACC-9"
    # The imported grid is not proven to share space with the existing series.
    assert is_ingested_uid(first.FrameOfReferenceUID)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_patient_details_override_the_synthetic_identity(tmp_path, no_sync):
    path = _write_volume(tmp_path)
    job_id, checksum = _start_job(path)
    orthanc = FakeOrthanc()

    await service.run_ingestion(
        job_id,
        source_path=path,
        filename=path.name,
        checksum=checksum,
        modality="CT",
        patient_id="STUDY-042",
        patient_name="SMITH^JOHN",
        orthanc=orthanc,
    )

    assert orthanc.datasets[0].PatientID == "STUDY-042"
    assert str(orthanc.datasets[0].PatientName) == "SMITH^JOHN"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_identity_without_a_study_is_reproducible():
    orthanc = FakeOrthanc()
    checksum = "9" * 64

    first = await service.resolve_identity(
        orthanc,
        checksum=checksum,
        study_instance_uid=None,
        patient_id=None,
        patient_name=None,
    )
    second = await service.resolve_identity(
        orthanc,
        checksum=checksum,
        study_instance_uid=None,
        patient_id=None,
        patient_name=None,
    )

    assert isinstance(first, SeriesIdentity)
    assert first.patient_id == second.patient_id
    assert first.study_instance_uid == second.study_instance_uid


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_corrupt_file_fails_the_job_without_leaving_a_series(tmp_path, no_sync):
    path = tmp_path / "broken.nii.gz"
    path.write_bytes(b"this is not a volume")
    job_id = jobs.job_id_for_checksum("7" * 64)
    jobs.create_job(
        job_id, filename=path.name, checksum="7" * 64, modality="CT", size_bytes=20
    )
    orthanc = FakeOrthanc()

    record = await service.run_ingestion(
        job_id,
        source_path=path,
        filename=path.name,
        checksum="7" * 64,
        modality="CT",
        orthanc=orthanc,
    )

    assert record["status"] == jobs.FAILED
    assert orthanc.datasets == []
    assert not path.exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_connection_drop_mid_upload_still_cleans_up_the_partial_series(
    tmp_path, no_sync
):
    """The exact failure real uploads hit: a dropped connection partway
    through a long slice loop, well before ``find_series`` is ever reached.

    Without a cleanup fallback keyed on the series UID the conversion already
    knows, the slices that *did* land stay in Orthanc, and Orthanc's own
    stable-series hook registers that partial series as if it were a whole
    study.
    """
    path = _write_volume(tmp_path)
    job_id, checksum = _start_job(path)
    orthanc = FakeOrthanc()

    real_upload = orthanc.upload_dataset
    calls = {"count": 0}

    async def flaky_upload(dataset):
        calls["count"] += 1
        if calls["count"] == 2:
            raise ConnectionError("Server disconnected without sending a response")
        return await real_upload(dataset)

    orthanc.upload_dataset = flaky_upload

    record = await service.run_ingestion(
        job_id,
        source_path=path,
        filename=path.name,
        checksum=checksum,
        modality="CT",
        orthanc=orthanc,
    )

    assert record["status"] == jobs.FAILED
    # One slice landed before the drop; the fallback must find and remove it
    # by series UID even though find_series() in the happy path was never
    # reached.
    assert orthanc.deleted == ["orthanc-series-1"]
    assert orthanc.datasets == []
