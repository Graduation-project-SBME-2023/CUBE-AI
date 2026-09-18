"""Concurrency limits for ingestion, owned by ingestion.

Ingestion used to borrow the pipeline's shared ResourceScheduler so a volume
conversion could not compete with a radiomics run for the same cores. That
scheduler has since been removed along with the concurrent-pipeline work it
belonged to, but the two limits it provided here are still worth keeping, and
neither of them needs the machinery that was removed:

``orthanc_io``
    A volume upload is hundreds of sequential HTTP requests. Several of those
    loops at once is what saturates Orthanc's own HTTP server and drops
    connections mid-upload, leaving a half-written series behind.

``cpu_heavy``
    Converting a volume to DICOM is CPU-bound and runs in a worker thread, so
    nothing else bounds how many run at once.

Plain semaphores are enough: these are process-local counts, exactly as the
scheduler's were, and ingestion is the only caller. The interface matches the
scheduler's ``lease`` so callers and tests did not have to change -- including
the keyword arguments it accepted for logging, which are ignored here.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterable

logger = logging.getLogger(__name__)

_DEFAULTS = {"orthanc_io": 2, "cpu_heavy": 1}


def _capacity(name: str) -> int:
    """Configured capacity for one resource, never below 1.

    A zero or negative value would deadlock every ingestion rather than
    disabling the limit, so it is treated as a misconfiguration.
    """
    raw = os.environ.get(f"INGEST_{name.upper()}_CAPACITY")
    try:
        value = int(raw) if raw is not None else _DEFAULTS[name]
    except (TypeError, ValueError):
        logger.warning(
            "Ignoring INGEST_%s_CAPACITY=%r: not an integer", name.upper(), raw
        )
        value = _DEFAULTS[name]
    return max(1, value)


class _Limiter:
    """Named semaphores, created on first use."""

    def __init__(self) -> None:
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def _get(self, name: str) -> asyncio.Semaphore:
        # Built lazily: a semaphore binds to the running loop, and this module
        # is imported at process start, before there is one.
        if name not in self._semaphores:
            self._semaphores[name] = asyncio.Semaphore(_capacity(name))
        return self._semaphores[name]

    @asynccontextmanager
    async def lease(
        self,
        resources: Iterable[str],
        **_ignored: Any,
    ) -> AsyncIterator[None]:
        """Hold every named resource for the duration of the block.

        Resources are acquired in sorted order so two callers asking for the
        same pair can never deadlock against each other.
        """
        held: list[asyncio.Semaphore] = []
        try:
            for name in sorted(set(resources)):
                semaphore = self._get(name)
                await semaphore.acquire()
                held.append(semaphore)
            yield None
        finally:
            for semaphore in held:
                semaphore.release()


ingestion_limiter = _Limiter()
