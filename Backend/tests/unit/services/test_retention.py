"""The transcript retention sweep, with a fake purge: no database."""

from __future__ import annotations

import asyncio

import pytest

from application_api.schemas.agent_transcript_entries import TranscriptPurgeOut
from application_api.services import retention

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _batches(*runs: int):
    calls = []

    async def purge(batch: int) -> TranscriptPurgeOut:
        calls.append(batch)
        count = runs[len(calls) - 1]
        return TranscriptPurgeOut(runs=count, entries=count * 3)

    return purge, calls


async def test_a_sweep_repeats_until_a_batch_comes_back_short():
    purge, calls = _batches(2, 2, 1, 2)
    result = await retention.purge_expired_transcripts(purge, batch=2)
    assert calls == [2, 2, 2]
    assert result == TranscriptPurgeOut(runs=5, entries=15, main_sessions=0)


async def test_a_sweep_is_bounded(monkeypatch):
    monkeypatch.setattr(retention, "MAX_BATCHES_PER_SWEEP", 3)
    purge, calls = _batches(*[1] * 10)
    result = await retention.purge_expired_transcripts(purge, batch=1)
    assert len(calls) == 3 and result.runs == 3


async def test_a_full_batch_of_archived_sessions_sweeps_again():
    calls = []

    async def purge(batch: int) -> TranscriptPurgeOut:
        calls.append(batch)
        return TranscriptPurgeOut(runs=0, entries=4, main_sessions=2 if len(calls) == 1 else 0)

    result = await retention.purge_expired_transcripts(purge, batch=2)
    assert len(calls) == 2 and result.main_sessions == 2


async def test_a_non_positive_interval_disables_the_timer(monkeypatch):
    async def never():
        raise AssertionError("the sweep must not run")

    monkeypatch.setattr(retention, "purge_expired_transcripts", never)
    await retention.purge_transcripts_forever(0)


async def test_a_failed_sweep_is_retried_on_the_next_tick(monkeypatch, caplog):
    sweeps = []

    async def sweep():
        sweeps.append(1)
        if len(sweeps) == 1:
            raise RuntimeError("database restarting")
        return TranscriptPurgeOut(runs=0, entries=0)

    async def tick(_seconds):
        if len(sweeps) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(retention, "purge_expired_transcripts", sweep)
    monkeypatch.setattr(retention.asyncio, "sleep", tick)
    with pytest.raises(asyncio.CancelledError):
        await retention.purge_transcripts_forever(60)
    assert len(sweeps) == 2
    assert "purge failed" in caplog.text
