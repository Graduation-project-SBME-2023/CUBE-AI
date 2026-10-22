"""jobs_core's copies of the toolkit's rules stay equal to the toolkit (Python 3.13 only)."""

from __future__ import annotations

import pytest

from agent_system.toolkit.context import Caller, ToolContext
from agent_system.toolkit.events import ProgressPayload
from agent_system.toolkit.manifest import (
    MAX_PRIORITY,
    MIN_PRIORITY,
    RESOURCE_QUEUES,
    TOOL_NAME_PATTERN,
)
from jobs_core import contract
from jobs_core.contract import JobProgress, JobRequest, make_job_id, make_submission_key

pytestmark = pytest.mark.unit


def test_job_name_pattern_is_the_tool_name_pattern() -> None:
    assert contract.JOB_NAME_PATTERN == TOOL_NAME_PATTERN.pattern


def test_resource_classes_are_the_toolkit_queues() -> None:
    assert contract.RESOURCE_CLASSES == tuple(queue for _, queue in RESOURCE_QUEUES)


def test_priority_bounds_are_the_toolkit_bounds() -> None:
    assert (contract.MIN_PRIORITY, contract.MAX_PRIORITY) == (MIN_PRIORITY, MAX_PRIORITY)


def test_job_progress_carries_the_toolkit_progress_fields() -> None:
    # The supervisor relays a JobProgress as a tool_progress ProgressPayload.
    assert set(JobProgress.model_fields) == set(ProgressPayload.model_fields) | {"seq"}


def test_a_real_tool_context_payload_is_accepted() -> None:
    run_id = "7d1f5c1e-0000-4000-8000-000000000001"
    args = {"folds": 5}
    job_id = make_job_id(run_id, "train_classifier", args)
    context = ToolContext(session_id="s-1", caller=Caller.NODE, run_id=run_id, job_id=job_id)

    request = JobRequest(
        job_id=job_id,
        submission_key=make_submission_key(job_id, 1),
        attempt=1,
        job_name="train_classifier",
        job_version=1,
        args=args,
        context=context.to_payload(),
        resource_class="compute.cpu",
        timeout_s=600.0,
        queue_timeout_s=None,
        priority=0,
    )
    assert ToolContext.from_payload(request.context) == context
