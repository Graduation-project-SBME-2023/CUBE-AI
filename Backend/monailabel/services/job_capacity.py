import asyncio
import os
from contextlib import asynccontextmanager


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


class JobCapacity:
    def __init__(self) -> None:
        self.max_concurrency = _positive_int("INFERENCE_MAX_CONCURRENCY", 1)
        self.queue_capacity = _positive_int("INFERENCE_QUEUE_CAPACITY", 8)
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._lock = asyncio.Lock()
        self._reserved = 0

    async def reserve(self) -> bool:
        async with self._lock:
            if self._reserved >= self.max_concurrency + self.queue_capacity:
                return False
            self._reserved += 1
            return True

    async def _release_reservation(self) -> None:
        async with self._lock:
            self._reserved = max(0, self._reserved - 1)

    @asynccontextmanager
    async def execution(self):
        try:
            await self._semaphore.acquire()
        except BaseException:
            await self._release_reservation()
            raise
        try:
            yield
        finally:
            self._semaphore.release()
            await self._release_reservation()

    @property
    def capacity(self) -> int:
        return self.max_concurrency + self.queue_capacity


inference_capacity = JobCapacity()
