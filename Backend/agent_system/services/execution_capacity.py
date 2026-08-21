import asyncio
import os
from collections import deque
from dataclasses import dataclass


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class ExecutionAdmission:
    accepted: bool
    queued: bool
    queue_position: int | None = None


class ExecutionCapacity:
    """Process-local FIFO admission control for expensive pipeline runs."""

    def __init__(
        self,
        max_concurrency: int | None = None,
        queue_capacity: int | None = None,
    ) -> None:
        self.max_concurrency = max_concurrency or _positive_int(
            "PIPELINE_MAX_CONCURRENCY", 1
        )
        self.queue_capacity = queue_capacity or _positive_int(
            "PIPELINE_QUEUE_CAPACITY", 8
        )
        self._lock = asyncio.Lock()
        self._running: set[str] = set()
        self._waiting: deque[str] = deque()
        self._waiters: dict[str, asyncio.Future[None]] = {}

    @classmethod
    def from_environment(
        cls,
        max_concurrency_name: str,
        queue_capacity_name: str,
        *,
        default_max_concurrency: int,
        default_queue_capacity: int,
    ) -> "ExecutionCapacity":
        return cls(
            max_concurrency=_positive_int(
                max_concurrency_name,
                default_max_concurrency,
            ),
            queue_capacity=_positive_int(
                queue_capacity_name,
                default_queue_capacity,
            ),
        )

    async def reserve(self, session_id: str) -> ExecutionAdmission:
        async with self._lock:
            if session_id in self._running:
                return ExecutionAdmission(accepted=False, queued=False)
            if session_id in self._waiters:
                return ExecutionAdmission(
                    accepted=False,
                    queued=True,
                    queue_position=self._position_unlocked(session_id),
                )

            if len(self._running) < self.max_concurrency:
                self._running.add(session_id)
                return ExecutionAdmission(accepted=True, queued=False)

            if len(self._waiting) >= self.queue_capacity:
                return ExecutionAdmission(accepted=False, queued=False)

            future = asyncio.get_running_loop().create_future()
            self._waiting.append(session_id)
            self._waiters[session_id] = future
            return ExecutionAdmission(
                accepted=True,
                queued=True,
                queue_position=len(self._waiting),
            )

    async def wait_for_turn(self, session_id: str) -> None:
        async with self._lock:
            if session_id in self._running:
                return
            waiter = self._waiters.get(session_id)
            if waiter is None:
                raise asyncio.CancelledError()
        await waiter

    async def release(self, session_id: str) -> None:
        async with self._lock:
            self._running.discard(session_id)
            waiter = self._waiters.pop(session_id, None)
            if waiter is not None:
                try:
                    self._waiting.remove(session_id)
                except ValueError:
                    pass
                if not waiter.done():
                    waiter.cancel()
            self._promote_unlocked()

    async def status(self, session_id: str) -> dict[str, int | str | None]:
        async with self._lock:
            if session_id in self._running:
                state = "RUNNING"
                position = None
            elif session_id in self._waiters:
                state = "QUEUED"
                position = self._position_unlocked(session_id)
            else:
                state = "NOT_RESERVED"
                position = None
            return {
                "state": state,
                "queue_position": position,
                "running": len(self._running),
                "queued": len(self._waiting),
                "max_concurrency": self.max_concurrency,
                "queue_capacity": self.queue_capacity,
            }

    def _position_unlocked(self, session_id: str) -> int | None:
        try:
            return list(self._waiting).index(session_id) + 1
        except ValueError:
            return None

    def _promote_unlocked(self) -> None:
        while len(self._running) < self.max_concurrency and self._waiting:
            next_session = self._waiting.popleft()
            waiter = self._waiters.pop(next_session, None)
            if waiter is None or waiter.cancelled():
                continue
            self._running.add(next_session)
            if not waiter.done():
                waiter.set_result(None)
