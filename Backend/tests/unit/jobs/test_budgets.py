"""The phase budgets of a job wait and the outer budget around them."""

from __future__ import annotations

import math

import pytest

from jobs_core.budgets import (
    DEFAULT_MARGIN_S,
    DEFAULT_RESULT_S,
    DEFAULT_UNAVAILABLE_S,
    JobBudgets,
)
from jobs_core.contract import JobRequest, make_job_id, make_submission_key

pytestmark = pytest.mark.unit

RUN_ID = "7d1f5c1e-0000-4000-8000-000000000001"


def _budgets(**overrides: float) -> JobBudgets:
    fields = {"queue_s": 60.0, "run_s": 600.0, "result_s": 30.0, "unavailable_s": 120.0}
    fields.update(overrides)
    return JobBudgets(**fields)


def _request(queue_timeout_s: float | None) -> JobRequest:
    job_id = make_job_id(RUN_ID, "train_classifier", {})
    return JobRequest(
        job_id=job_id,
        submission_key=make_submission_key(job_id, 1),
        attempt=1,
        job_name="train_classifier",
        job_version=1,
        args={},
        context={"run_id": RUN_ID},
        resource_class="compute.cpu",
        timeout_s=900.0,
        queue_timeout_s=queue_timeout_s,
        priority=0,
    )


def test_the_outer_budget_is_the_sum_plus_the_margin() -> None:
    assert _budgets(margin_s=15.0).outer_s == 60 + 600 + 30 + 120 + 15


def test_the_outer_budget_is_never_smaller_than_any_inner_one() -> None:
    budgets = _budgets(margin_s=0.0)
    inner = (budgets.queue_s, budgets.run_s, budgets.result_s, budgets.unavailable_s)
    assert budgets.outer_s == sum(inner)
    assert all(budgets.outer_s >= value for value in inner)


def test_the_margin_has_a_default() -> None:
    assert _budgets().margin_s == DEFAULT_MARGIN_S


def test_an_unlimited_queue_wait_makes_the_outer_budget_unlimited() -> None:
    assert _budgets(queue_s=math.inf).outer_s == math.inf


@pytest.mark.parametrize("name", ["queue_s", "run_s", "result_s", "unavailable_s"])
@pytest.mark.parametrize("value", [0.0, -1.0, math.nan])
def test_a_phase_budget_must_be_positive(name: str, value: float) -> None:
    with pytest.raises(ValueError, match=name):
        _budgets(**{name: value})


@pytest.mark.parametrize("name", ["run_s", "result_s", "unavailable_s"])
def test_only_the_queue_wait_may_be_unlimited(name: str) -> None:
    with pytest.raises(ValueError, match="only queue_s"):
        _budgets(**{name: math.inf})


@pytest.mark.parametrize("value", [-1.0, math.inf, math.nan])
def test_the_margin_must_be_finite_and_not_negative(value: float) -> None:
    with pytest.raises(ValueError, match="margin_s"):
        _budgets(margin_s=value)


@pytest.mark.parametrize("value", [True, "60", None])
def test_a_budget_must_be_a_number(value: object) -> None:
    with pytest.raises(TypeError, match="queue_s"):
        _budgets(queue_s=value)  # type: ignore[arg-type]


def test_budgets_from_a_request_use_its_own_limits() -> None:
    budgets = JobBudgets.for_request(_request(queue_timeout_s=45.0))

    assert (budgets.queue_s, budgets.run_s) == (45.0, 900.0)
    assert (budgets.result_s, budgets.unavailable_s, budgets.margin_s) == (
        DEFAULT_RESULT_S,
        DEFAULT_UNAVAILABLE_S,
        DEFAULT_MARGIN_S,
    )


def test_a_request_without_a_queue_limit_waits_without_one() -> None:
    budgets = JobBudgets.for_request(_request(queue_timeout_s=None))
    assert budgets.queue_s == math.inf


def test_the_waiter_budgets_can_be_set() -> None:
    budgets = JobBudgets.for_request(
        _request(queue_timeout_s=45.0), result_s=5.0, unavailable_s=6.0, margin_s=7.0
    )
    assert budgets.outer_s == 45 + 900 + 5 + 6 + 7
