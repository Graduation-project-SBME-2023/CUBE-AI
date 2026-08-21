import asyncio
import logging
import os
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

ResourceNotifier = Callable[[dict[str, Any]], Awaitable[None]]


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


class ResourceCapacityExceeded(RuntimeError):
    """Raised when the bounded resource wait queue is full."""


@dataclass(frozen=True)
class ResourceLease:
    request_id: str
    session_id: str
    resources: tuple[str, ...]
    operation: str
    agent_id: str
    queued: bool


@dataclass
class _ResourceRequest:
    lease: ResourceLease
    future: asyncio.Future[None]


class ResourceScheduler:
    """Atomic, process-local admission control for expensive pipeline stages."""

    def __init__(
        self,
        capacities: dict[str, int] | None = None,
        *,
        max_waiters: int | None = None,
        notifier: ResourceNotifier | None = None,
    ) -> None:
        configured = capacities or {
            "gpu_segmentation": _positive_int(
                "RESOURCE_GPU_SEGMENTATION_CAPACITY", 1
            ),
            "cpu_heavy": _positive_int("RESOURCE_CPU_HEAVY_CAPACITY", 1),
        }
        if not configured or any(value < 1 for value in configured.values()):
            raise ValueError("Resource capacities must be positive")

        self.capacities = dict(configured)
        self.max_waiters = max_waiters or _positive_int(
            "RESOURCE_WAIT_QUEUE_CAPACITY", 32
        )
        self._notifier = notifier
        self._lock = asyncio.Lock()
        self._in_use = {name: 0 for name in self.capacities}
        self._active: dict[str, ResourceLease] = {}
        self._waiting: deque[_ResourceRequest] = deque()

    def set_notifier(self, notifier: ResourceNotifier | None) -> None:
        self._notifier = notifier

    @asynccontextmanager
    async def lease(
        self,
        resources: Iterable[str],
        *,
        session_id: str,
        operation: str,
        agent_id: str,
    ):
        acquired = await self.acquire(
            resources,
            session_id=session_id,
            operation=operation,
            agent_id=agent_id,
        )
        try:
            yield acquired
        finally:
            await self.release(acquired)

    async def acquire(
        self,
        resources: Iterable[str],
        *,
        session_id: str,
        operation: str,
        agent_id: str,
    ) -> ResourceLease:
        requested = tuple(sorted(set(resources)))
        if not requested:
            raise ValueError("At least one resource is required")
        unknown = set(requested) - set(self.capacities)
        if unknown:
            raise ValueError(f"Unknown resources: {sorted(unknown)}")

        session_key = str(session_id or "").strip()
        if not session_key:
            raise ValueError("session_id is required")

        loop = asyncio.get_running_loop()
        request_id = str(uuid4())
        queued = False
        queue_position = None

        async with self._lock:
            conflicts_with_waiter = any(
                set(requested) & set(waiter.lease.resources)
                for waiter in self._waiting
                if not waiter.future.cancelled()
            )
            if self._can_grant_unlocked(requested) and not conflicts_with_waiter:
                lease = ResourceLease(
                    request_id=request_id,
                    session_id=session_key,
                    resources=requested,
                    operation=operation,
                    agent_id=agent_id,
                    queued=False,
                )
                self._grant_unlocked(lease)
                future = None
            else:
                if len(self._waiting) >= self.max_waiters:
                    raise ResourceCapacityExceeded(
                        "Resource wait queue is full; retry when capacity is available"
                    )
                queued = True
                lease = ResourceLease(
                    request_id=request_id,
                    session_id=session_key,
                    resources=requested,
                    operation=operation,
                    agent_id=agent_id,
                    queued=True,
                )
                future = loop.create_future()
                self._waiting.append(_ResourceRequest(lease=lease, future=future))
                queue_position = len(self._waiting)

        if queued:
            try:
                await self._notify(lease, "WAITING_FOR_RESOURCE", queue_position)
                await future
            except BaseException:
                await self._cancel_request(request_id)
                raise

        try:
            await self._notify(lease, "ACQUIRED", None)
        except BaseException:
            await self.release(lease)
            raise
        return lease

    async def release(self, lease: ResourceLease) -> None:
        promoted: list[ResourceLease] = []
        async with self._lock:
            active = self._active.pop(lease.request_id, None)
            if active is None:
                return
            for resource in active.resources:
                self._in_use[resource] = max(0, self._in_use[resource] - 1)
            promoted = self._promote_unlocked()

        await self._notify(active, "RELEASED", None)
        for promoted_lease in promoted:
            logger.info(
                "Promoted resource request session=%s operation=%s resources=%s",
                promoted_lease.session_id,
                promoted_lease.operation,
                promoted_lease.resources,
            )

    async def status(self, session_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            waiting = [
                request.lease
                for request in self._waiting
                if not request.future.cancelled()
            ]
            active = list(self._active.values())
            if session_id is not None:
                key = str(session_id)
                waiting = [lease for lease in waiting if lease.session_id == key]
                active = [lease for lease in active if lease.session_id == key]

            return {
                "resources": {
                    name: {
                        "capacity": capacity,
                        "in_use": self._in_use[name],
                        "available": capacity - self._in_use[name],
                    }
                    for name, capacity in self.capacities.items()
                },
                "active": [self._serialize(lease) for lease in active],
                "waiting": [
                    {
                        **self._serialize(lease),
                        "queue_position": index + 1,
                    }
                    for index, lease in enumerate(waiting)
                ],
                "total_waiting": sum(
                    not request.future.cancelled() for request in self._waiting
                ),
                "max_waiters": self.max_waiters,
            }

    async def shutdown(self) -> None:
        async with self._lock:
            waiting = tuple(self._waiting)
            self._waiting.clear()
            for request in waiting:
                if not request.future.done():
                    request.future.cancel()

    async def _cancel_request(self, request_id: str) -> None:
        released: ResourceLease | None = None
        async with self._lock:
            self._waiting = deque(
                request
                for request in self._waiting
                if request.lease.request_id != request_id
            )
            released = self._active.pop(request_id, None)
            if released is not None:
                for resource in released.resources:
                    self._in_use[resource] = max(0, self._in_use[resource] - 1)
            self._promote_unlocked()

        if released is not None:
            await self._notify(released, "RELEASED", None)

    def _can_grant_unlocked(self, resources: Iterable[str]) -> bool:
        return all(
            self._in_use[resource] < self.capacities[resource]
            for resource in resources
        )

    def _grant_unlocked(self, lease: ResourceLease) -> None:
        for resource in lease.resources:
            self._in_use[resource] += 1
        self._active[lease.request_id] = lease

    def _promote_unlocked(self) -> list[ResourceLease]:
        promoted: list[ResourceLease] = []
        remaining: deque[_ResourceRequest] = deque()
        blocked_resources: set[str] = set()

        for request in self._waiting:
            if request.future.cancelled():
                continue
            resources = set(request.lease.resources)
            if not resources & blocked_resources and self._can_grant_unlocked(resources):
                self._grant_unlocked(request.lease)
                promoted.append(request.lease)
                if not request.future.done():
                    request.future.set_result(None)
            else:
                blocked_resources.update(resources)
                remaining.append(request)

        self._waiting = remaining
        return promoted

    async def _notify(
        self,
        lease: ResourceLease,
        state: str,
        queue_position: int | None,
    ) -> None:
        if self._notifier is None:
            return
        payload = {
            **self._serialize(lease),
            "state": state,
            "queue_position": queue_position,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await self._notifier(payload)
        except Exception:
            logger.warning("Resource status notification failed", exc_info=True)

    @staticmethod
    def _serialize(lease: ResourceLease) -> dict[str, Any]:
        return {
            "request_id": lease.request_id,
            "session_id": lease.session_id,
            "resources": list(lease.resources),
            "operation": lease.operation,
            "agent": lease.agent_id,
        }


resource_scheduler = ResourceScheduler()
