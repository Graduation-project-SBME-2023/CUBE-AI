"""The files of one job's directory, and the one atomic way to write its result.

A compute job keeps four JSON files in its directory:

request.json   the ``JobRequest`` it was started with
progress.json  the latest ``JobProgress`` (written by the supervisor, never the child)
process.json   where the child runs (host, pid, process group) and the job heartbeat
result.json    how it ended; once complete it is the answer, whatever the queue says

The directory belongs to the job id, so files from an earlier try of the same
job can still be there. ``process.json`` and ``result.json`` therefore carry the
``submission_key`` (and the result also the ``attempt_id``) they were written
for, and a reader compares them with the try it asks about.

A reader returns ``None`` when its file does not exist and raises
``JobProtocolError`` when the file exists but is not valid: a broken file is a
fault to report, not a missing one.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .contract import ERROR_CODES_BY_STATUS, ErrorCode, JobProgress, JobRequest, canonical_json
from .status import JobProtocolError, JobStatus, is_final

logger = logging.getLogger(__name__)

REQUEST_FILE = "request.json"
PROGRESS_FILE = "progress.json"
PROCESS_FILE = "process.json"
RESULT_FILE = "result.json"

# Temporary files start with a dot, so a listing of the job dir never shows a
# half-written one under a real name, and a start-up sweep can find leftovers.
TEMP_PREFIX = "."
TEMP_SUFFIX = ".tmp"


class ProcessInfo(BaseModel):
    """``process.json``: the child process of one try, and its heartbeat.

    submission_key  the try this child runs
    host            the machine it runs on (a process group means nothing elsewhere)
    pid, pgid       the child and its process group, which a stop kills whole
    heartbeat_at    the last time the supervisor saw the child alive; rewritten every tick
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    submission_key: str = Field(min_length=1)
    host: str = Field(min_length=1)
    pid: int = Field(gt=0)
    pgid: int = Field(gt=0)
    heartbeat_at: AwareDatetime = Field(strict=False)


class JobResultFile(BaseModel):
    """``result.json``: how one try ended.

    submission_key, attempt_id   the try it was written for; a reader ignores any other
    status       a terminal ``JobStatus``
    error_code   set exactly when the status failed (see ERROR_CODES_BY_STATUS)
    error        a one-line reason, never a traceback
    result       the tool's JSON result, inline
    result_ref   or the name of a file in the job dir holding a large one
    finished_at  when the try ended
    """

    # Statuses, codes and times are strings in the file, so those fields alone
    # are parsed (strict=False); a bad value is still refused.
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)

    submission_key: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    status: JobStatus = Field(strict=False)
    error_code: ErrorCode | None = Field(default=None, strict=False)
    error: str | None = None
    result: dict[str, Any] | None = None
    result_ref: str | None = None
    finished_at: AwareDatetime = Field(strict=False)

    @field_validator("result")
    @classmethod
    def _result_is_json(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            try:
                canonical_json(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"must be JSON only: {exc}") from None
        return value

    @field_validator("result_ref")
    @classmethod
    def _ref_is_a_file_name(cls, value: str | None) -> str | None:
        # A name inside the job dir, so a result can never point elsewhere.
        if value is not None and (not value or Path(value).name != value or value in {".", ".."}):
            raise ValueError("result_ref must be a plain file name inside the job dir")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> JobResultFile:
        if not is_final(self.status):
            raise ValueError(f"a result needs a terminal status, got {self.status.value}")
        allowed = ERROR_CODES_BY_STATUS.get(self.status, frozenset())
        if allowed and self.error_code not in allowed:
            names = ", ".join(sorted(code.value for code in allowed))
            raise ValueError(f"status {self.status.value} needs an error_code in: {names}")
        if not allowed and self.error_code is not None:
            raise ValueError(f"status {self.status.value} carries no error_code")
        if self.result is not None and self.result_ref is not None:
            raise ValueError("set result or result_ref, not both")
        return self

    def belongs_to(self, submission_key: str, attempt_id: str) -> bool:
        """Return True when this result was written for exactly that try."""
        return self.submission_key == submission_key and self.attempt_id == attempt_id


_Model = TypeVar("_Model", bound=BaseModel)


def _read(path: Path, model: type[_Model]) -> _Model | None:
    """Return the file parsed as ``model``, ``None`` if it does not exist.

    Raises ``JobProtocolError`` when it exists but is not valid JSON of that shape.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        return model.model_validate_json(data)
    except ValidationError as exc:
        raise JobProtocolError(f"{path.name} is not a valid {model.__name__}: {exc}") from None


def read_request(job_dir: Path) -> JobRequest | None:
    """Return the job's ``request.json``, or ``None`` if it was not written."""
    return _read(job_dir / REQUEST_FILE, JobRequest)


def read_progress(job_dir: Path) -> JobProgress | None:
    """Return the job's latest ``progress.json``, or ``None`` before the first update."""
    return _read(job_dir / PROGRESS_FILE, JobProgress)


def read_process(job_dir: Path) -> ProcessInfo | None:
    """Return the job's ``process.json``, or ``None`` before the child was launched."""
    return _read(job_dir / PROCESS_FILE, ProcessInfo)


def read_result(job_dir: Path) -> JobResultFile | None:
    """Return the job's ``result.json``, or ``None`` if no try has finished.

    The file may belong to an earlier try; check ``belongs_to()`` before using it.
    """
    return _read(job_dir / RESULT_FILE, JobResultFile)


def _fsync_dir(directory: Path) -> None:
    """Flush a rename in ``directory`` to disk, where the OS allows it.

    Linux needs this for the new name to survive a power loss; Windows cannot
    open a directory this way, so there it is skipped.
    """
    if os.name != "posix":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_atomic(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` so a reader sees the old file or the new one, never half.

    Writes a temporary file in the same directory, flushes it to disk, then
    renames it over ``path`` (a rename within one directory is atomic). If any
    step fails, the temporary file is removed and ``path`` is left as it was.
    """
    fd, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f"{TEMP_PREFIX}{path.name}.", suffix=TEMP_SUFFIX
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def write_result(
    job_dir: Path,
    *,
    submission_key: str,
    attempt_id: str,
    status: JobStatus,
    finished_at: datetime,
    error_code: ErrorCode | None = None,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    result_ref: str | None = None,
) -> JobResultFile:
    """Write ``result.json`` for one try, atomically, and return what was written.

    The file is stamped with ``submission_key`` and ``attempt_id`` so a reader
    asking about another try ignores it. The fields are checked before anything
    touches the disk (``JobResultFile``): a non-terminal status, a missing or
    stray error code, or a non-JSON result raises ``pydantic.ValidationError``
    and leaves any existing ``result.json`` as it was. An existing file is
    replaced, whichever try wrote it.
    """
    record = JobResultFile(
        submission_key=submission_key,
        attempt_id=attempt_id,
        status=status,
        error_code=error_code,
        error=error,
        result=result,
        result_ref=result_ref,
        finished_at=finished_at,
    )
    _write_atomic(job_dir / RESULT_FILE, record.model_dump_json().encode("utf-8"))
    logger.debug("Wrote %s for %s: %s", RESULT_FILE, submission_key, record.status.value)
    return record
