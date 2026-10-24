"""The JobClient rules, held by the in-memory backend: submit, wait, cancel, list."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from jobs_core.backends.in_memory import InMemoryJobClient
from jobs_core.budgets import JobBudgets
from jobs_core.client import (
    JobClient,
    JobConflict,
    JobNotFound,
    JobUnavailable,
    NoResult,
)
from jobs_core.contract import ErrorCode, JobProgress, JobRequest, make_job_id, make_submission_key
from jobs_core.polling import RetryPolicy
from jobs_core.status import JobProtocolError, JobStatus

pytestmark = pytest.mark.unit

RUN_ID = "7d1f5c1e-0000-4000-8000-000000000001"
OTHER_RUN = "7d1f5c1e-0000-4000-8000-000000000002"
EPOCH = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)
BUDGETS = JobBudgets(queue_s=30.0, run_s=60.0, result_s=10.0, unavailable_s=20.0, margin_s=5.0)


class FakeTime:
    """A monotonic clock that moves only when someone sleeps, and the wall clock with it."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def wall(self) -> datetime:
        return EPOCH + timedelta(seconds=self.now)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Token:
    """Stands in for the toolkit's CancelToken: anything with ``cancelled``."""

    def __init__(self) -> None:
        self.cancelled = False


def _client(time: FakeTime, **kwargs: Any) -> InMemoryJobClient:
    return InMemoryJobClient(
        clock=time.clock,
        sleep=time.sleep,
        now=time.wall,
        poll_interval_s=1.0,
        retry=RetryPolicy(base_s=1.0, cap_s=8.0, jitter=False),
        **kwargs,
    )


def _request(
    attempt: int = 1,
    *,
    reusable: bool = False,
    args: dict[str, Any] | None = None,
    job_version: int = 1,
    run_id: str = RUN_ID,
    resource_class: str = "compute.cpu",
) -> JobRequest:
    args = {"folds": 5} if args is None else args
    job_id = make_job_id(run_id, "train_classifier", args)
    return JobRequest(
        job_id=job_id,
        submission_key=make_submission_key(job_id, attempt),
        attempt=attempt,
        job_name="train_classifier",
        job_version=job_version,
        args=args,
        context={"run_id": run_id},
        resource_class=resource_class,
        reusable=reusable,
        timeout_s=60.0,
        queue_timeout_s=30.0,
        priority=0,
    )


def _script(client: InMemoryJobClient, key: str, *steps: Callable[[], object] | None) -> None:
    """Run ``steps[n]`` before the n-th read of ``key`` (``None`` = nothing changes)."""
    queue = list(steps)

    def on_get(read_key: str) -> None:
        if read_key == key and queue:
            step = queue.pop(0)
            if step is not None:
                step()

    client.on_get = on_get


def run(coro):
    return asyncio.run(coro)


# --- the contract


def test_the_fake_offers_every_method_of_the_contract() -> None:
    # A JobClient method missing here, or with other parameters, would let
    # code tested on the fake break on a real backend.
    for name, member in inspect.getmembers(JobClient, inspect.isfunction):
        if name.startswith("_"):
            continue
        fake = getattr(InMemoryJobClient, name)
        assert inspect.iscoroutinefunction(fake), name
        assert list(inspect.signature(fake).parameters) == list(
            inspect.signature(member).parameters
        ), name


# --- submit


def test_a_new_request_is_queued() -> None:
    client = _client(FakeTime())
    record = run(client.submit(_request()))

    assert record.status is JobStatus.QUEUED
    assert (record.id, record.attempt) == (_request().job_id, 1)
    assert record.input_hash == _request().input_hash
    assert record.queued_at == EPOCH


def test_the_same_key_returns_the_record_it_already_has() -> None:
    client = _client(FakeTime())
    request = _request()
    run(client.submit(request))
    client.start(request.submission_key)

    again = run(client.submit(request))
    assert again.status is JobStatus.RUNNING
    assert len(run(client.list(RUN_ID))) == 1


def test_the_same_job_with_other_inputs_is_a_conflict() -> None:
    client = _client(FakeTime())
    run(client.submit(_request(attempt=1, job_version=1)))

    # The tool was upgraded between attempts of one run.
    with pytest.raises(JobConflict, match="other inputs"):
        run(client.submit(_request(attempt=2, job_version=2)))


def test_a_reusable_request_takes_an_earlier_success() -> None:
    client = _client(FakeTime())
    first = _request(attempt=1, reusable=True)
    run(client.submit(first))
    client.start(first.submission_key)
    client.finish(first.submission_key, JobStatus.SUCCEEDED, result={"mask": "m1"})

    second = _request(attempt=2, reusable=True)
    record = run(client.submit(second))

    assert (record.status, record.attempt) == (JobStatus.SUCCEEDED, 1)
    assert run(client.get(second.submission_key)) == record
    assert run(client.result(second.submission_key)) == {"mask": "m1"}
    assert len(run(client.list(RUN_ID))) == 1  # nothing ran again


def test_a_reusable_request_re_attaches_to_an_earlier_try_still_running() -> None:
    client = _client(FakeTime())
    first = _request(attempt=1, reusable=True)
    run(client.submit(first))
    client.start(first.submission_key)

    record = run(client.submit(_request(attempt=2, reusable=True)))
    assert (record.status, record.attempt) == (JobStatus.RUNNING, 1)


def test_a_reusable_request_runs_again_after_an_earlier_failure() -> None:
    client = _client(FakeTime())
    first = _request(attempt=1, reusable=True)
    run(client.submit(first))
    client.finish(first.submission_key, JobStatus.FAILED, error_code=ErrorCode.OOM, error="oom")

    record = run(client.submit(_request(attempt=2, reusable=True)))
    assert (record.status, record.attempt) == (JobStatus.QUEUED, 2)


def test_a_job_that_is_not_reusable_runs_again_even_after_a_success() -> None:
    client = _client(FakeTime())
    first = _request(attempt=1)
    run(client.submit(first))
    client.finish(first.submission_key, JobStatus.SUCCEEDED, result={})

    record = run(client.submit(_request(attempt=2)))
    assert (record.status, record.attempt) == (JobStatus.QUEUED, 2)


def test_a_full_queue_is_retried_until_it_has_room() -> None:
    time = FakeTime()
    client = _client(time, queue_capacity=1, retry_after_s=3.0)
    blocker = _request(args={"folds": 1})
    run(client.submit(blocker))
    _free_after(client, time, blocker.submission_key, seconds=5.0)

    record = run(client.submit(_request(), unavailable_s=20.0))

    assert record.status is JobStatus.QUEUED
    assert time.sleeps == [3.0, 3.0]  # the backend's Retry-After, twice


def test_a_queue_that_stays_full_is_resource_unavailable() -> None:
    time = FakeTime()
    client = _client(time, queue_capacity=1)
    run(client.submit(_request(args={"folds": 1})))

    with pytest.raises(JobUnavailable) as caught:
        run(client.submit(_request(), unavailable_s=10.0))

    assert caught.value.error_code is ErrorCode.RESOURCE_UNAVAILABLE
    assert time.now == 10.0
    assert len(run(client.list(RUN_ID))) == 1  # the refused job was never queued


def _free_after(client: InMemoryJobClient, time: FakeTime, key: str, *, seconds: float) -> None:
    """Start ``key`` (freeing its queue place) once ``seconds`` have passed."""
    real_sleep = client.sleep

    async def sleep_then_free(delay: float) -> None:
        await real_sleep(delay)
        if time.now >= seconds and run_status(client, key) is JobStatus.QUEUED:
            client.start(key)

    client.sleep = sleep_then_free


def run_status(client: InMemoryJobClient, key: str) -> JobStatus:
    return client._entry(client._resolve(key)).record.status


# --- wait


def test_wait_returns_the_terminal_record_and_each_progress_once() -> None:
    time = FakeTime()
    client = _client(time)
    request = _request()
    run(client.submit(request))
    key = request.submission_key
    seen: list[JobProgress] = []
    _script(
        client,
        key,
        None,
        lambda: client.start(key),
        lambda: client.report(key, 1, 3, "fold 1"),
        None,  # no new progress: not reported again
        lambda: (client.report(key, 2, 3), client.report(key, 3, 3)),  # two between reads
        lambda: client.finish(key, JobStatus.SUCCEEDED, result={"auc": 0.8}),
    )

    record = run(client.wait(key, budgets=BUDGETS, on_progress=seen.append))

    assert record.status is JobStatus.SUCCEEDED
    assert record.result == {"auc": 0.8}
    # Polling sees the latest update only: seq 1 was overwritten before a read.
    assert [progress.seq for progress in seen] == [0, 2]


def test_an_async_progress_listener_is_awaited() -> None:
    time = FakeTime()
    client = _client(time)
    request = _request()
    run(client.submit(request))
    key = request.submission_key
    seen: list[int] = []

    async def listener(progress: JobProgress) -> None:
        seen.append(progress.seq)

    _script(
        client,
        key,
        lambda: (client.start(key), client.report(key, 1, 1)),
        lambda: client.finish(key, JobStatus.SUCCEEDED, result={}),
    )
    run(client.wait(key, budgets=BUDGETS, on_progress=listener))
    assert seen == [0]


def test_a_broken_progress_listener_does_not_end_the_wait() -> None:
    time = FakeTime()
    client = _client(time)
    request = _request()
    run(client.submit(request))
    key = request.submission_key

    def listener(progress: JobProgress) -> None:
        raise RuntimeError("the UI went away")

    _script(
        client,
        key,
        lambda: (client.start(key), client.report(key, 1, 1)),
        lambda: client.finish(key, JobStatus.SUCCEEDED, result={}),
    )
    assert run(client.wait(key, budgets=BUDGETS, on_progress=listener)).status is (
        JobStatus.SUCCEEDED
    )


def test_a_job_queued_past_its_budget_is_cancelled_as_a_queue_timeout() -> None:
    time = FakeTime()
    client = _client(time)
    request = _request()
    run(client.submit(request))

    record = run(client.wait(request.submission_key, budgets=BUDGETS))

    assert (record.status, record.error_code) == (JobStatus.TIMED_OUT, ErrorCode.QUEUE_TIMEOUT)
    assert client.cancel_requests == [request.submission_key]
    assert time.now == 30.0


def test_a_job_running_past_its_budget_is_cancelled_as_a_timeout() -> None:
    time = FakeTime()
    client = _client(time)
    request = _request()
    run(client.submit(request))
    key = request.submission_key
    _script(client, key, None, None, lambda: client.start(key))  # starts at t = 2 s

    record = run(client.wait(key, budgets=BUDGETS))

    assert (record.status, record.error_code) == (JobStatus.TIMED_OUT, ErrorCode.TIMEOUT)
    # The run budget counts from the read that first saw it running, not from the
    # submit: the time it spent queued is not taken from its run time.
    assert time.now == 2.0 + 60.0
    assert client.cancel_requests == [key]


def test_a_backend_down_too_long_is_resource_unavailable() -> None:
    time = FakeTime()
    client = _client(time)
    request = _request()
    run(client.submit(request))
    key = request.submission_key
    client.start(key)
    _script(client, key, lambda: setattr(client, "unavailable", True))

    record = run(client.wait(key, budgets=BUDGETS))

    assert (record.status, record.error_code) == (
        JobStatus.FAILED,
        ErrorCode.RESOURCE_UNAVAILABLE,
    )
    # Backoff 1, 2, 4, 8, 8 s: down for 20 s at the read after the fifth wait (23 s).
    assert time.sleeps == [1.0, 1.0, 2.0, 4.0, 8.0, 8.0]


def test_the_backend_s_retry_after_paces_the_wait_during_an_outage() -> None:
    time = FakeTime()
    client = _client(time, retry_after_s=5.0)
    request = _request()
    run(client.submit(request))
    key = request.submission_key
    client.start(key)
    _script(client, key, lambda: setattr(client, "unavailable", True))

    run(client.wait(key, budgets=BUDGETS))
    # The interval once, then the backend's 5 s instead of the 1, 2, 4... backoff.
    assert time.sleeps == [1.0, 5.0, 5.0, 5.0, 5.0]


def test_a_short_outage_does_not_end_the_wait() -> None:
    time = FakeTime()
    client = _client(time)
    request = _request()
    run(client.submit(request))
    key = request.submission_key
    client.start(key)
    calls = {"n": 0}
    real_get = client.get

    async def flaky_get(submission_key: str):
        calls["n"] += 1
        client.unavailable = calls["n"] in (2, 3)
        if calls["n"] == 5:
            client.unavailable = False
            client.finish(key, JobStatus.SUCCEEDED, result={})
        return await real_get(submission_key)

    client.get = flaky_get  # type: ignore[method-assign]
    assert run(client.wait(key, budgets=BUDGETS)).status is JobStatus.SUCCEEDED


def test_an_unknown_status_keeps_the_waiter_waiting() -> None:
    # A newer backend reports a status this library does not know. The job may
    # still be running on a GPU, so the waiter must neither stop nor resubmit.
    time = FakeTime()
    client = _client(time)
    request = _request()
    run(client.submit(request))
    key = request.submission_key
    client.start(key)
    _script(
        client,
        key,
        lambda: client.report_raw_status(key, "WARMING_UP"),
        None,
        None,
        lambda: client.finish(key, JobStatus.SUCCEEDED, result={"ok": True}),
    )

    record = run(client.wait(key, budgets=BUDGETS))

    assert record.status is JobStatus.SUCCEEDED
    assert client.cancel_requests == []


def test_a_protocol_error_at_the_deadline_cancels_and_fails() -> None:
    time = FakeTime()
    client = _client(time)
    request = _request()
    run(client.submit(request))
    key = request.submission_key
    client.start(key)
    _script(client, key, None, lambda: client.report_raw_status(key, "WARMING_UP"))

    record = run(client.wait(key, budgets=BUDGETS))

    assert (record.status, record.error_code) == (JobStatus.FAILED, ErrorCode.PROTOCOL_ERROR)
    assert "WARMING_UP" in (record.error or "")
    assert client.cancel_requests == [key]
    assert time.now == 60.0  # the run budget, counted from the first read


def test_protocol_errors_from_the_first_read_raise_once_the_wait_is_over() -> None:
    time = FakeTime()
    client = _client(time)
    request = _request()
    run(client.submit(request))
    client.report_raw_status(request.submission_key, "WARMING_UP")

    with pytest.raises(JobProtocolError):
        run(client.wait(request.submission_key, budgets=BUDGETS))
    assert time.now == BUDGETS.outer_s
    assert client.cancel_requests == [request.submission_key]


def test_a_cancel_signal_cancels_once_and_waits_for_the_stop() -> None:
    time = FakeTime()
    client = _client(time)
    request = _request()
    run(client.submit(request))
    key = request.submission_key
    client.start(key)
    token = Token()
    _script(
        client,
        key,
        lambda: setattr(token, "cancelled", True),
        None,
        lambda: client.finish(key, JobStatus.CANCELLED, error_code=ErrorCode.CANCELLED),
    )

    record = run(client.wait(key, budgets=BUDGETS, cancel=token))

    assert record.status is JobStatus.CANCELLED
    assert client.cancel_requests == [key]  # once, although the token stayed set


def test_a_job_already_finished_returns_at_once() -> None:
    time = FakeTime()
    client = _client(time)
    request = _request()
    run(client.submit(request))
    client.finish(request.submission_key, JobStatus.FAILED, error_code=ErrorCode.OOM, error="oom")

    record = run(client.wait(request.submission_key, budgets=BUDGETS))
    assert (record.status, record.error_code) == (JobStatus.FAILED, ErrorCode.OOM)
    assert time.sleeps == []


def test_waiting_on_an_unknown_key_fails_at_once() -> None:
    with pytest.raises(JobNotFound):
        run(_client(FakeTime()).wait("nope.1", budgets=BUDGETS))


# --- cancel, result, purge, list


def test_cancelling_a_queued_try_ends_it() -> None:
    client = _client(FakeTime())
    request = _request()
    run(client.submit(request))

    record = run(client.cancel(request.submission_key))
    assert (record.status, record.error_code) == (JobStatus.CANCELLED, ErrorCode.CANCELLED)


def test_cancelling_a_running_try_asks_it_to_stop() -> None:
    client = _client(FakeTime())
    request = _request()
    run(client.submit(request))
    client.start(request.submission_key)

    assert run(client.cancel(request.submission_key)).status is JobStatus.CANCELLING


def test_cancelling_a_finished_try_changes_nothing() -> None:
    client = _client(FakeTime())
    request = _request()
    run(client.submit(request))
    done = client.finish(request.submission_key, JobStatus.SUCCEEDED, result={})

    assert run(client.cancel(request.submission_key)) == done


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.FAILED])
def test_only_a_success_has_a_result(status: JobStatus) -> None:
    client = _client(FakeTime())
    request = _request()
    run(client.submit(request))
    if status is JobStatus.FAILED:
        client.finish(request.submission_key, status, error_code=ErrorCode.ERROR, error="boom")

    with pytest.raises(NoResult, match=status.value):
        run(client.result(request.submission_key))


def test_purge_forgets_the_try_and_its_aliases() -> None:
    client = _client(FakeTime())
    first = _request(attempt=1, reusable=True)
    run(client.submit(first))
    client.finish(first.submission_key, JobStatus.SUCCEEDED, result={})
    second = _request(attempt=2, reusable=True)
    run(client.submit(second))

    run(client.purge(second.submission_key))

    for key in (first.submission_key, second.submission_key):
        with pytest.raises(JobNotFound):
            run(client.get(key))
    run(client.purge("unknown.1"))  # ignored


def test_list_returns_the_run_s_tries_and_can_keep_only_the_active_ones() -> None:
    client = _client(FakeTime())
    done, running, queued = (_request(args={"folds": n}) for n in (1, 2, 3))
    for request in (done, running, queued):
        run(client.submit(request))
    run(client.submit(_request(run_id=OTHER_RUN)))
    client.finish(done.submission_key, JobStatus.SUCCEEDED, result={})
    client.start(running.submission_key)

    assert len(run(client.list(RUN_ID))) == 3
    active = run(client.list(RUN_ID, active_only=True))
    assert {record.status for record in active} == {JobStatus.RUNNING, JobStatus.QUEUED}
    assert run(client.list("no-such-run")) == []


def test_a_monai_request_is_served_too() -> None:
    client = _client(FakeTime())
    request = _request(reusable=True, resource_class="monai.gpu")
    assert run(client.submit(request)).status is JobStatus.QUEUED
