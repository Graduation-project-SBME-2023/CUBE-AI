"""Job records are the only channel a background conversion has to the caller."""

from __future__ import annotations

import json

import pytest

from ingestion import jobs


@pytest.mark.unit
def test_job_id_is_stable_for_the_same_file():
    checksum = "a" * 64
    assert jobs.job_id_for_checksum(checksum) == jobs.job_id_for_checksum(checksum)
    assert jobs.job_id_for_checksum(checksum) != jobs.job_id_for_checksum("b" * 64)


@pytest.mark.unit
def test_create_read_and_update_round_trip():
    job_id = jobs.job_id_for_checksum("c" * 64)

    jobs.create_job(
        job_id, filename="scan.nii.gz", checksum="c" * 64, modality="ct", size_bytes=17
    )
    jobs.update_job(job_id, status=jobs.UPLOADING, slice_count=42)
    record = jobs.read_job(job_id)

    assert record["status"] == jobs.UPLOADING
    assert record["slice_count"] == 42
    assert record["modality"] == "CT"
    assert record["filename"] == "scan.nii.gz"
    assert record["created_at"] and record["updated_at"]


@pytest.mark.unit
def test_reading_an_unknown_job_returns_none():
    assert jobs.read_job("0123456789abcdef") is None


@pytest.mark.unit
@pytest.mark.parametrize("job_id", ["", "../escape", "job id", "x" * 200, "a/b"])
def test_unsafe_job_ids_are_rejected(job_id):
    # The id reaches the filesystem, so path traversal must be impossible.
    with pytest.raises(ValueError):
        jobs.job_path(job_id)


@pytest.mark.unit
def test_writes_are_atomic_and_leave_no_temporary_files():
    job_id = jobs.job_id_for_checksum("d" * 64)

    jobs.create_job(
        job_id, filename="a.nii.gz", checksum="d" * 64, modality="MR", size_bytes=1
    )
    jobs.update_job(job_id, status=jobs.COMPLETED)

    path = jobs.job_path(job_id)
    leftovers = [item for item in path.parent.iterdir() if item.suffix == ".tmp"]
    assert leftovers == []
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == jobs.COMPLETED


@pytest.mark.unit
def test_fail_job_records_the_stage_and_message():
    job_id = jobs.job_id_for_checksum("e" * 64)
    jobs.create_job(
        job_id, filename="a.nii.gz", checksum="e" * 64, modality="CT", size_bytes=1
    )

    record = jobs.fail_job(job_id, "verification", "geometry mismatch")

    assert record["status"] == jobs.FAILED
    assert record["stage"] == "verification"
    assert record["error"] == "geometry mismatch"


@pytest.mark.unit
def test_warnings_accumulate_instead_of_replacing():
    job_id = jobs.job_id_for_checksum("f" * 64)
    jobs.create_job(
        job_id, filename="a.nii.gz", checksum="f" * 64, modality="CT", size_bytes=1
    )

    jobs.append_warnings(job_id, ["first"])
    jobs.append_warnings(job_id, ["second", "  "])

    assert jobs.read_job(job_id)["warnings"] == ["first", "second"]


@pytest.mark.unit
def test_prune_removes_only_finished_jobs():
    import os
    import time

    finished = jobs.job_id_for_checksum("1" * 64)
    running = jobs.job_id_for_checksum("2" * 64)
    for job_id, status in ((finished, jobs.COMPLETED), (running, jobs.UPLOADING)):
        jobs.create_job(
            job_id, filename="a.nii.gz", checksum=job_id, modality="CT", size_bytes=1
        )
        jobs.update_job(job_id, status=status)
        old = time.time() - 10_000
        os.utime(jobs.job_path(job_id), (old, old))

    removed = jobs.prune_finished_jobs(max_age_seconds=3600)

    assert removed == 1
    assert jobs.read_job(finished) is None
    assert jobs.read_job(running) is not None
