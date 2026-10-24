"""The job contract: how a job is named, requested and recorded.

Two ids name a job, and they mean different things:

* ``job_id`` names the *work*: the same run asking for the same tool with the same
  arguments always gets the same id, so a resumed run re-attaches instead of
  computing again.
* ``submission_key`` names one *try* of that work: ``{job_id}.{attempt_number}``,
  stored in ``run_attempts.submission_key``.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .status import JobStatus

# Copies of the toolkit's rules. jobs_core cannot import agent_system (the
# Application API image does not contain it), so test_contract_matches_toolkit.py
# checks that these stay equal to toolkit/manifest.py.
JOB_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"  # = TOOL_NAME_PATTERN
MIN_PRIORITY = 0
MAX_PRIORITY = 9

# Where a job runs. The compute.* classes are the job queue's queues (the same
# names as the toolkit's RESOURCE_QUEUES); monai.gpu is MONAI Label, which owns
# the GPU, so there is no compute queue for the GPU.
RESOURCE_CLASSES = ("compute.highmem", "compute.cpu", "monai.gpu")

ResourceClass = Literal["compute.highmem", "compute.cpu", "monai.gpu"]


class ErrorCode(StrEnum):
    """Why a job did not succeed: the closed set the Node Runner and the Watchdog decide on.

    error                 the handler failed, or the supervisor itself crashed
    timeout               it ran longer than ``timeout_s``
    queue_timeout         it waited longer than ``queue_timeout_s`` and never started
    oom                   out of memory (``MemoryError``, or killed with exit code 137)
    interrupted           the worker died or shut down while the job ran
    cancelled             someone cancelled it
    run_not_running       its attempt was no longer the run's active one when it started
    resource_unavailable  the backend kept refusing for now (MONAI busy, the queue full)
                          past the ``unavailable_s`` budget
    protocol_error        the backend kept reporting something outside the contract (an
                          unknown status) until the waiter's budget ran out; the waiter
                          cancelled the job rather than guess whether it still runs
    """

    ERROR = "error"
    TIMEOUT = "timeout"
    QUEUE_TIMEOUT = "queue_timeout"
    OOM = "oom"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    RUN_NOT_RUNNING = "run_not_running"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    PROTOCOL_ERROR = "protocol_error"


# Which error codes each status may carry. A status missing here (QUEUED,
# RUNNING, CANCELLING, SUCCEEDED) carries none; the others must carry one.
ERROR_CODES_BY_STATUS: dict[JobStatus, frozenset[ErrorCode]] = {
    JobStatus.FAILED: frozenset(
        {
            ErrorCode.ERROR,
            ErrorCode.OOM,
            ErrorCode.RUN_NOT_RUNNING,
            ErrorCode.RESOURCE_UNAVAILABLE,
            ErrorCode.PROTOCOL_ERROR,
        }
    ),
    JobStatus.TIMED_OUT: frozenset({ErrorCode.TIMEOUT, ErrorCode.QUEUE_TIMEOUT}),
    JobStatus.CANCELLED: frozenset({ErrorCode.CANCELLED}),
    JobStatus.INTERRUPTED: frozenset({ErrorCode.INTERRUPTED}),
}


def canonical_json(value: Any) -> bytes:
    """The one byte encoding a job id is taken over.

    Frozen: changing any option here changes every job id, so a resumed run
    would no longer find its own job. Keys sorted, no spaces, UTF-8, and no
    NaN or Infinity (they are not JSON).
    """
    text = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return text.encode("utf-8")


def make_job_id(run_id: str, job_name: str, args: dict[str, Any]) -> str:
    """``sha256(run_id | job_name | canonical_json(args))`` as 64 hex characters.

    Not checked here: neither ``run_id`` (a UUID) nor ``job_name`` (a tool name,
    ``TOOL_NAME_PATTERN``) can contain ``|``, so the parts never run together.
    """
    payload = b"|".join(
        [
            run_id.encode("utf-8"),
            job_name.encode("utf-8"),
            canonical_json(args),
        ]
    )
    return hashlib.sha256(payload).hexdigest()


def make_submission_key(job_id: str, attempt_number: int) -> str:
    """``{job_id}.{attempt_number}``; attempt numbers start at 1, as in ``run_attempts``."""
    if attempt_number < 1:
        raise ValueError(f"attempt number must be >= 1, got {attempt_number}")
    return f"{job_id}.{attempt_number}"


def make_input_hash(job_name: str, job_version: int, args: dict[str, Any]) -> str:
    """``sha256(canonical_json({job_name, job_version, args}))``: what decides the output.

    The job id already covers the args, so a second submit with the same id
    can differ only here: the tool was upgraded between attempts. ``submit``
    refuses that as a conflict instead of mixing two versions' results.
    """
    inputs = {"job_name": job_name, "job_version": job_version, "args": args}
    return hashlib.sha256(canonical_json(inputs)).hexdigest()


class JobRequest(BaseModel):
    """One try of a job, as sent to the worker and stored on the attempt.

    It crosses a process boundary as JSON, so every field is JSON, and it is
    checked whole: the ids must be the ones the helpers above compute from the
    other fields, so two requests for the same work can never disagree on them.

    job_id           make_job_id(context["run_id"], job_name, args)
    submission_key   make_submission_key(job_id, attempt)
    attempt          the run_attempts number, from 1
    job_name         a registered job name, never an import path: an
                     ``execution="job"`` tool, or a MONAI job kind (``monai_infer``)
    job_version      the tool's ToolSpec.version
    args             the tool's arguments (JSON only)
    context          ToolContext.to_payload(); must name the node run
    resource_class   where it runs: a compute queue (ToolSpec.resource_class) or monai.gpu
    reusable         true only for a job that writes nothing but its result, so a
                     later attempt may take an earlier attempt's success instead of
                     running again; a job that writes rows or files is never reusable
    timeout_s        run budget, from the spec
    queue_timeout_s  how long it may wait to start; None = no limit
    priority         MIN_PRIORITY..MAX_PRIORITY, from the spec
    """

    # strict: no silent conversions ("2" is not an attempt number).
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)

    job_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    submission_key: str
    attempt: int = Field(ge=1)
    job_name: str = Field(pattern=JOB_NAME_PATTERN)
    job_version: int = Field(ge=1)
    args: dict[str, Any]
    context: dict[str, Any]
    resource_class: ResourceClass
    reusable: bool
    timeout_s: float = Field(gt=0)
    queue_timeout_s: float | None = Field(gt=0)
    priority: int = Field(ge=MIN_PRIORITY, le=MAX_PRIORITY)

    @field_validator("args", "context")
    @classmethod
    def _json_only(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            canonical_json(value)
        except (TypeError, ValueError) as exc:
            # pydantic reports a ValueError as a validation error, not a TypeError.
            raise ValueError(f"must be JSON only: {exc}") from None
        return value

    @model_validator(mode="after")
    def _ids_match(self) -> JobRequest:
        run_id = self.context.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("context.run_id must name the node run the job belongs to")
        if self.job_id != make_job_id(run_id, self.job_name, self.args):
            raise ValueError("job_id is not make_job_id(context.run_id, job_name, args)")
        if self.submission_key != make_submission_key(self.job_id, self.attempt):
            raise ValueError("submission_key is not make_submission_key(job_id, attempt)")
        if self.context.get("job_id") not in (None, self.job_id):
            raise ValueError("context.job_id names a different job")
        return self

    @property
    def input_hash(self) -> str:
        # A property, not a field: it is derived, so it is never sent or stored twice.
        return make_input_hash(self.job_name, self.job_version, self.args)


class JobProgress(BaseModel):
    """The latest progress of a running job: the toolkit's ``ProgressPayload``
    fields plus ``seq``, which counts updates so a stream reader drops one it has seen."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)

    seq: int = Field(ge=0)
    current: int | float = Field(ge=0)
    total: int = Field(ge=0)
    label: str | None = None


class JobRecord(BaseModel):
    """What a backend knows about one try of a job: built from the queue view row
    plus the job dir (Procrastinate), or from MONAI's job store.

    id            the job id (``JobRequest.job_id``, or MONAI's job id)
    status        a ``JobStatus``
    error_code    set exactly when the status failed; see ERROR_CODES_BY_STATUS
    attempt       the try this record describes, from 1
    input_hash    ``make_input_hash(...)`` of the request (conflict check)
    progress      the latest ``JobProgress``, if any was reported
    queued_at, started_at, finished_at   timezone-aware; started/finished once reached
    error         a human-readable reason, never a traceback
    result        a small JSON result, inline
    result_ref    or the name of a file in the job dir holding a large one (claim check)
    host          the machine that ran it, once started
    """

    # Rows and HTTP bodies carry statuses, codes and times as strings, so those
    # fields alone are parsed (strict=False); a bad value is still refused.
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)

    id: str = Field(min_length=1)
    status: JobStatus = Field(strict=False)
    error_code: ErrorCode | None = Field(strict=False)
    attempt: int = Field(ge=1)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    progress: JobProgress | None = None
    queued_at: AwareDatetime = Field(strict=False)
    started_at: AwareDatetime | None = Field(default=None, strict=False)
    finished_at: AwareDatetime | None = Field(default=None, strict=False)
    error: str | None = None
    result: dict[str, Any] | None = None
    result_ref: str | None = None
    host: str | None = None

    @field_validator("result")
    @classmethod
    def _result_is_json(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            try:
                canonical_json(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"must be JSON only: {exc}") from None
        return value

    @model_validator(mode="after")
    def _consistent(self) -> JobRecord:
        allowed = ERROR_CODES_BY_STATUS.get(self.status, frozenset())
        if allowed and self.error_code not in allowed:
            names = ", ".join(sorted(code.value for code in allowed))
            raise ValueError(f"status {self.status.value} needs an error_code in: {names}")
        if not allowed and self.error_code is not None:
            raise ValueError(f"status {self.status.value} carries no error_code")
        if self.result is not None and self.result_ref is not None:
            raise ValueError("set result or result_ref, not both")
        for name, earlier, later in (
            ("started_at", self.queued_at, self.started_at),
            ("finished_at", self.started_at or self.queued_at, self.finished_at),
        ):
            if later is not None and later < earlier:
                raise ValueError(f"{name} is before the time it follows")
        return self
