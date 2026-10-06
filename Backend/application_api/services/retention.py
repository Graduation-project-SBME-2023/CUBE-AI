"""Retention sweeps run from the application lifespan.

The agent-checkpoint purge deletes in batches, so one sweep repeats it until
a batch comes back short. A failed sweep is logged and retried on the next
tick: the timer must outlive a database restart.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from application_api.db.session import AsyncSessionLocal
from application_api.repositories import agent_checkpoints as checkpoints_repo
from application_api.schemas.agent_checkpoints import PurgeOut

logger = logging.getLogger(__name__)

PURGE_BATCH = 500
# A runaway guard: one sweep never deletes more than this many batches.
MAX_BATCHES_PER_SWEEP = 100

PurgeBatch = Callable[[int], Awaitable[PurgeOut]]


async def _purge_batch(batch: int) -> PurgeOut:
    async with AsyncSessionLocal() as db:
        return await checkpoints_repo.purge_checkpoints(db, batch)


async def purge_expired_checkpoints(
    purge: PurgeBatch = _purge_batch, batch: int = PURGE_BATCH
) -> PurgeOut:
    """One sweep: purge batch after batch until one comes back short."""
    runs = checkpoints = 0
    for _ in range(MAX_BATCHES_PER_SWEEP):
        result = await purge(batch)
        runs += result.runs
        checkpoints += result.checkpoints
        if result.runs < batch:
            break
    return PurgeOut(runs=runs, checkpoints=checkpoints)


async def purge_checkpoints_forever(interval_seconds: float) -> None:
    """Sweep at startup and then on a timer; a non-positive interval disables it."""
    if interval_seconds <= 0:
        return
    while True:
        try:
            await purge_expired_checkpoints()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Agent-checkpoint purge failed; retrying in %.0fs", interval_seconds)
        await asyncio.sleep(interval_seconds)
