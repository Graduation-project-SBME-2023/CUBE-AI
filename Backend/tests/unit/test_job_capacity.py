import asyncio

import pytest

from monailabel.services.job_capacity import JobCapacity


@pytest.mark.asyncio
async def test_capacity_rejects_work_beyond_running_and_queue(monkeypatch):
    monkeypatch.setenv("INFERENCE_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("INFERENCE_QUEUE_CAPACITY", "2")
    capacity = JobCapacity()

    assert await capacity.reserve() is True
    assert await capacity.reserve() is True
    assert await capacity.reserve() is True
    assert await capacity.reserve() is False


@pytest.mark.asyncio
async def test_execution_releases_reserved_capacity(monkeypatch):
    monkeypatch.setenv("INFERENCE_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("INFERENCE_QUEUE_CAPACITY", "1")
    capacity = JobCapacity()

    assert await capacity.reserve() is True
    async with capacity.execution():
        assert await capacity.reserve() is True
        assert await capacity.reserve() is False

    # One reservation remains; the released execution slot can be reserved again.
    assert await capacity.reserve() is True
