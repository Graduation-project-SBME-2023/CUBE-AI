"""Job-root ownership and atomic metadata writes —."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# A job in one of these states is finished. Its metadata is history.
TERMINAL_STATUSES = frozenset(
    {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "INTERRUPTED"}
)
ACTIVE_STATUSES = frozenset({"QUEUED", "RUNNING", "CANCELLING"})


class JobRootBusy(RuntimeError):
    """Another process already owns this job root."""


class JobRootLock:
    """An exclusive, OS-level lock on one job root."""

    def __init__(self, root: Path, name: str = ".owner.lock") -> None:
        self.root = Path(root)
        self.path = self.root / name
        self._handle = None

    def acquire(self) -> "JobRootLock":
        self.root.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if os.name == "nt":  # pragma: no cover - exercised on Windows only
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise JobRootBusy(
                f"Another process already owns the job root {self.root}. Refusing to "
                "start a second scheduler: startup marks in-flight jobs INTERRUPTED, "
                "and two owners would do that to each other's live work."
            ) from exc

        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "acquired_at": datetime.now(timezone.utc).isoformat(),
                }
            ).encode("utf-8")
        )
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle
        logger.info("Acquired job-root lock %s (pid %s)", self.path, os.getpid())
        return self

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":  # pragma: no cover
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            logger.warning("Could not release job-root lock %s", self.path)
        finally:
            self._handle.close()
            self._handle = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def owner(self) -> dict:
        """Who holds this lock, read through the lock's own handle."""
        if self._handle is None:
            return {}
        self._handle.seek(0)
        try:
            return json.loads(self._handle.read().decode("utf-8") or "{}")
        except (OSError, ValueError):
            return {}

    def __enter__(self) -> "JobRootLock":
        return self.acquire()

    def __exit__(self, *_exc_info) -> None:
        self.release()


def write_json_atomically(target: Path, temporary: Path, payload: dict) -> None:
    """Write metadata so a crash leaves either the old file or the new one."""
    temporary.parent.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    # Best effort: not every platform allows opening a directory.
    try:  # pragma: no cover - platform dependent
        directory = os.open(str(target.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        pass


def is_terminal(job: dict | None) -> bool:
    return bool(job) and str(job.get("status") or "") in TERMINAL_STATUSES


def guard_terminal(job: dict | None, new_status: str) -> None:
    """Refuse to rewrite a finished job's outcome."""
    if not is_terminal(job):
        return
    current = str(job.get("status"))
    if current == new_status:
        return
    raise JobStateConflict(
        f"Job {job.get('job_id')} is {current}; a terminal job's outcome is "
        f"immutable and cannot become {new_status}."
    )


class JobStateConflict(RuntimeError):
    """An attempt to change a terminal job's recorded outcome."""
