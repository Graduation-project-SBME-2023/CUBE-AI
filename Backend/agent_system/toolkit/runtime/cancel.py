"""A cancel token: lets the Executor stop one node's work without cancelling
the task that runs it. Dispatch, agent engines and the job
runtime (Plan 5) all watch the same token."""

from __future__ import annotations

import asyncio


class CancelToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    async def wait(self) -> None:
        """Return once the token is cancelled."""
        await self._event.wait()


class ToolCancelled(Exception):
    """The call was stopped through its cancel token."""
