"""The job contract: how a job is named, requested and recorded (merged plan, Phase 5.1).

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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Copies of the toolkit's rules. jobs_core cannot import agent_system (the
# Application API image does not contain it), so test_contract_matches_toolkit.py
# checks that these stay equal to toolkit/manifest.py.
JOB_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"  # = TOOL_NAME_PATTERN
RESOURCE_CLASSES = ("compute.gpu", "compute.highmem", "compute.cpu")  # = RESOURCE_QUEUES
MIN_PRIORITY = 0
MAX_PRIORITY = 9

ResourceClass = Literal["compute.gpu", "compute.highmem", "compute.cpu"]


class ErrorCode(StrEnum):
    """Why a job did not succeed: the closed set the Node Runner and the Watchdog decide on.

    error                 the handler failed, or the supervisor itself crashed
    timeout               it ran longer than ``timeout_s``
    queue_timeout         it waited longer than ``queue_timeout_s`` and never started
    oom                   out of memory (``MemoryError``, or killed with exit code 137)
    interrupted           the worker died or shut down while the job ran
    cancelled             someone cancelled it
    run_not_running       its attempt was no longer the run's active one when it started (H20)
    resource_unavailable  MONAI stayed busy (429) past the ``unavailable_s`` budget
    """

    ERROR = "error"
    TIMEOUT = "timeout"
    QUEUE_TIMEOUT = "queue_timeout"
    OOM = "oom"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    RUN_NOT_RUNNING = "run_not_running"
    RESOURCE_UNAVAILABLE = "resource_unavailable"


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


class JobRequest(BaseModel):
    """One try of a job, as sent to the worker and stored on the attempt.

    It crosses a process boundary as JSON, so every field is JSON, and it is
    checked whole: the ids must be the ones the helpers above compute from the
    other fields, so two requests for the same work can never disagree on them.

    job_id           make_job_id(context["run_id"], job_name, args)
    submission_key   make_submission_key(job_id, attempt)
    attempt          the run_attempts number, from 1
    job_name         a registered tool name, never an import path
    job_version      the tool's ToolSpec.version
    args             the tool's arguments (JSON only)
    context          ToolContext.to_payload(); must name the node run
    resource_class   the queue, copied from ToolSpec.resource_class
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
