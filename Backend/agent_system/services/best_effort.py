import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any
from weakref import WeakKeyDictionary

from .timeout_policy import BEST_EFFORT_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

def _max_pending() -> int:
    try:
        return max(1, int(os.environ.get("BEST_EFFORT_MAX_PENDING", "128")))
    except (TypeError, ValueError):
        return 128


_MAX_PENDING = _max_pending()
_pending_by_loop: WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    set[asyncio.Task],
] = WeakKeyDictionary()


def _pending_for_current_loop() -> set[asyncio.Task]:
    loop = asyncio.get_running_loop()
    pending = _pending_by_loop.setdefault(loop, set())
    pending.difference_update(tuple(task for task in pending if task.done()))
    return pending


def initialize_best_effort() -> None:
    """Start with an empty task registry for the active application loop."""
    loop = asyncio.get_running_loop()
    stale = _pending_by_loop.pop(loop, set())
    for task in stale:
        if not task.done():
            task.cancel()
    _pending_by_loop[loop] = set()


async def _run(
    operation: Callable[[], Awaitable[Any]],
    *,
    name: str,
    timeout_seconds: float,
) -> None:
    try:
        await asyncio.wait_for(operation(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning("Best-effort operation %s timed out after %.1fs", name, timeout_seconds)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Best-effort operation %s failed: %s", name, exc)


def schedule_best_effort(
    operation: Callable[[], Awaitable[Any]],
    *,
    name: str,
    timeout_seconds: float = BEST_EFFORT_TIMEOUT_SECONDS,
) -> bool:
    """Run non-critical I/O without holding up the agent execution path."""
    pending = _pending_for_current_loop()
    if len(pending) >= _MAX_PENDING:
        logger.warning(
            "Dropping best-effort operation %s because %s writes are pending",
            name,
            len(pending),
        )
        return False

    loop = asyncio.get_running_loop()
    task = loop.create_task(
        _run(operation, name=name, timeout_seconds=timeout_seconds),
        name=f"best-effort:{name}",
    )
    pending.add(task)
    task.add_done_callback(pending.discard)
    return True


async def drain_best_effort(timeout_seconds: float = 2.0) -> None:
    """Give pending writes a short shutdown window without delaying forever."""
    loop = asyncio.get_running_loop()
    pending_for_loop = _pending_by_loop.get(loop)
    if not pending_for_loop:
        _pending_by_loop.pop(loop, None)
        return
    active = tuple(task for task in pending_for_loop if not task.done())
    if not active:
        _pending_by_loop.pop(loop, None)
        return
    _, timed_out = await asyncio.wait(active, timeout=timeout_seconds)
    pending = {
        task for task in pending_for_loop
        if not task.done()
    } | set(timed_out)
    if pending:
        logger.warning(
            "Cancelling %s best-effort operations during shutdown",
            len(pending),
        )
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    _pending_by_loop.pop(loop, None)
