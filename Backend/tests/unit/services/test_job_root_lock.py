"""The MONAI job root is a receipt, so it needs an owner."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[3]

# Loaded by path under a private name: `services` is already the agent
# system's namespace package here, and adding Backend/monailabel to sys.path
# would shadow it for every other test in the process.
_spec = importlib.util.spec_from_file_location(
    "_cubeai_monailabel_job_root", BACKEND_ROOT / "monailabel" / "services" / "job_root.py"
)
job_root = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(job_root)

JobRootBusy = job_root.JobRootBusy
JobRootLock = job_root.JobRootLock
JobStateConflict = job_root.JobStateConflict
guard_terminal = job_root.guard_terminal
is_terminal = job_root.is_terminal
write_json_atomically = job_root.write_json_atomically

pytestmark = pytest.mark.unit


def test_a_second_owner_of_the_job_root_is_refused(tmp_path):
    """Startup marks in-flight jobs INTERRUPTED. Two processes doing that to
    each other's live jobs is how a running inference becomes a lost one."""
    first = JobRootLock(tmp_path).acquire()
    try:
        with pytest.raises(JobRootBusy) as excinfo:
            JobRootLock(tmp_path).acquire()
        assert "already owns the job root" in str(excinfo.value)
    finally:
        first.release()

    # Released: the next owner may take it.
    second = JobRootLock(tmp_path).acquire()
    second.release()


def test_the_lock_records_its_owner(tmp_path):
    lock = JobRootLock(tmp_path).acquire()
    try:
        recorded = lock.owner()
        assert recorded["pid"] == os.getpid()
        assert recorded["acquired_at"]
    finally:
        lock.release()


def test_metadata_is_written_through_a_temporary_file(tmp_path):
    target = tmp_path / "jobs" / "job.json"
    temporary = tmp_path / "temp" / "job.json.tmp"

    write_json_atomically(target, temporary, {"job_id": "abc", "status": "RUNNING"})

    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "RUNNING"
    # The temporary file is renamed, not left behind to be mistaken for a job.
    assert not temporary.exists()

    write_json_atomically(target, temporary, {"job_id": "abc", "status": "SUCCEEDED"})
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "SUCCEEDED"


@pytest.mark.parametrize(
    "status", ["SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "INTERRUPTED"]
)
def test_a_finished_job_keeps_its_outcome(status):
    job = {"job_id": "abc", "status": status}
    assert is_terminal(job)

    with pytest.raises(JobStateConflict) as excinfo:
        guard_terminal(job, "CANCELLED" if status != "CANCELLED" else "FAILED")
    assert "immutable" in str(excinfo.value)

    # Re-asserting the same outcome is a retry of one write, not a change.
    guard_terminal(job, status)


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING", "CANCELLING"])
def test_an_active_job_may_still_transition(status):
    guard_terminal({"job_id": "abc", "status": status}, "SUCCEEDED")
    guard_terminal(None, "RUNNING")
