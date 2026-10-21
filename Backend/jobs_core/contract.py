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
from typing import Any


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
