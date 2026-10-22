"""Retention sweeps run from the application lifespan.

The transcript purge deletes in batches, so one sweep repeats it until a batch
comes back short. A failed sweep is logged and retried on the next tick: the
timer must outlive a database restart.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from application_api.db.session import AsyncSessionLocal
from application_api.repositories import agent_transcript_entries as transcripts_repo
from application_api.schemas.agent_transcript_entries import TranscriptPurgeOut

logger = logging.getLogger(__name__)

PURGE_BATCH = 500
# A runaway guard: one sweep never deletes more than this many batches.
MAX_BATCHES_PER_SWEEP = 100

PurgeBatch = Callable[[int], Awaitable[TranscriptPurgeOut]]


async def _purge_batch(batch: int) -> TranscriptPurgeOut:
    async with AsyncSessionLocal() as db:
        return await transcripts_repo.purge(db, batch)


async def purge_expired_transcripts(
    purge: PurgeBatch = _purge_batch, batch: int = PURGE_BATCH
) -> TranscriptPurgeOut:
    """One sweep: purge batch after batch until one comes back short."""
    runs = entries = sessions = 0
    for _ in range(MAX_BATCHES_PER_SWEEP):
        result = await purge(batch)
        runs += result.runs
        entries += result.entries
        sessions += result.main_sessions
        if result.runs < batch and result.main_sessions < batch:
            break
    return TranscriptPurgeOut(runs=runs, entries=entries, main_sessions=sessions)


async def purge_transcripts_forever(interval_seconds: float) -> None:
    """Sweep at startup and then on a timer; a non-positive interval disables it."""
    if interval_seconds <= 0:
        return
    while True:
        try:
            await purge_expired_transcripts()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Transcript purge failed; retrying in %.0fs", interval_seconds)
        await asyncio.sleep(interval_seconds)
