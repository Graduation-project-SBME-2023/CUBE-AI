"""Executor resource pools, read from ``config/resources.yaml``.

The file names each pool and its size. The Validator refuses a target that
declares a pool missing from the file, and ``Pools`` gives every pool one
asyncio semaphore of that size, shared by every session's Executor in this
worker. Pools are runtime only: a node waiting for a pool is never stored.
``CUBEAI_RESOURCES_FILE`` points at another file (tests, deployments).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import MappingProxyType

import yaml

DEFAULT_RESOURCES_FILE = Path(__file__).resolve().parent.parent / "config" / "resources.yaml"


class ResourceConfigError(ValueError):
    """``resources.yaml`` is missing or malformed."""


def resources_file() -> Path:
    return Path(os.environ.get("CUBEAI_RESOURCES_FILE") or DEFAULT_RESOURCES_FILE)


def load_pools(path: Path | None = None) -> Mapping[str, int]:
    """``{pool: size}`` from the file; read when called, never at import."""
    path = path or resources_file()
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ResourceConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("v") != 1:
        raise ResourceConfigError(f"{path}: expected a mapping with v: 1")
    pools = document.get("pools")
    if not isinstance(pools, dict) or not pools:
        raise ResourceConfigError(f"{path}: 'pools' must be a non-empty mapping")
    for name, size in pools.items():
        if not isinstance(name, str) or isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ResourceConfigError(f"{path}: pool {name!r} needs a size >= 1, not {size!r}")
    return MappingProxyType(dict(pools))


def pool_names(path: Path | None = None) -> frozenset[str]:
    return frozenset(load_pools(path))


class Lease:
    """The pool slots one node holds while it runs. Releasing it twice releases once."""

    def __init__(self, pools: Pools, names: frozenset[str]) -> None:
        self.pools = pools
        self.names = names
        self.released = False

    def release(self) -> None:
        self.pools.release(self)


class Pools:
    """One asyncio semaphore per pool, sized from ``resources.yaml``.

    A node takes every pool it declares or none (``try_acquire``), so two
    nodes never each hold half of what the other needs. A refused caller asks
    to be told when a slot is released (``notify_on_release``) instead of
    waiting on a semaphore, so one session waiting for the GPU never blocks
    another that only needs the CPU.
    """

    def __init__(self, sizes: Mapping[str, int]) -> None:
        self.sizes = MappingProxyType(dict(sizes))
        self._semaphores = {name: asyncio.Semaphore(size) for name, size in sizes.items()}
        self._listeners: list[Callable[[], None]] = []

    @classmethod
    def from_config(cls, path: Path | None = None) -> Pools:
        return cls(load_pools(path))

    def available(self, name: str) -> bool:
        return not self._semaphores[name].locked()

    async def try_acquire(self, names: Iterable[str]) -> Lease | None:
        """Take one slot of every pool in ``names``, or none and return ``None``.

        It never waits: each semaphore is checked first, and acquiring one that
        is not locked returns without suspending, so no other task can take a
        slot in between. A pool missing from the file raises ``ResourceConfigError``.
        """
        wanted = frozenset(names)
        unknown = sorted(wanted - self._semaphores.keys())
        if unknown:
            raise ResourceConfigError(f"unknown resource pools {unknown}")
        if not all(self.available(name) for name in wanted):
            return None
        for name in sorted(wanted):
            await self._semaphores[name].acquire()
        return Lease(self, wanted)

    def release(self, lease: Lease) -> None:
        """Give the lease's slots back and tell every caller that waits for a slot."""
        if lease.released:
            return
        lease.released = True
        for name in lease.names:
            self._semaphores[name].release()
        listeners, self._listeners = self._listeners, []
        for listener in listeners:
            listener()

    def notify_on_release(self, listener: Callable[[], None]) -> None:
        """Call ``listener`` once, at the next release of any slot."""
        self._listeners.append(listener)
