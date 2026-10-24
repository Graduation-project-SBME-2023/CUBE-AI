"""Resource pools: one asyncio semaphore per pool, taken all or nothing."""

from __future__ import annotations

import pytest

from agent_system.execution.resources import Pools, ResourceConfigError, load_pools

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_a_node_takes_every_pool_it_declares_or_none():
    pools = Pools({"llm": 1, "gpu": 1})
    gpu = await pools.try_acquire({"gpu"})

    assert await pools.try_acquire({"llm", "gpu"}) is None
    assert pools.available("llm")  # the refused call took nothing

    gpu.release()
    both = await pools.try_acquire({"llm", "gpu"})
    assert both is not None and not pools.available("llm") and not pools.available("gpu")


async def test_a_pool_admits_as_many_nodes_as_its_size():
    pools = Pools({"llm": 2})
    first, second = await pools.try_acquire({"llm"}), await pools.try_acquire({"llm"})

    assert first and second and await pools.try_acquire({"llm"}) is None


async def test_a_node_with_no_pools_always_starts():
    assert await Pools({"llm": 1}).try_acquire(()) is not None


async def test_an_unknown_pool_is_refused():
    with pytest.raises(ResourceConfigError):
        await Pools({"llm": 1}).try_acquire({"quantum"})


async def test_a_release_is_counted_once_and_tells_each_waiting_caller_once():
    pools = Pools({"llm": 1})
    told: list[str] = []
    lease = await pools.try_acquire({"llm"})
    pools.notify_on_release(lambda: told.append("a"))
    pools.notify_on_release(lambda: told.append("b"))

    lease.release()
    lease.release()  # a second release gives nothing back

    assert told == ["a", "b"]
    assert await pools.try_acquire({"llm"}) is not None and await pools.try_acquire({"llm"}) is None


async def test_the_pools_are_sized_from_the_resources_file(tmp_path):
    path = tmp_path / "resources.yaml"
    path.write_text("v: 1\npools:\n  llm: 3\n  gpu: 1\n", encoding="utf-8")

    pools = Pools.from_config(path)

    assert dict(pools.sizes) == dict(load_pools(path)) == {"llm": 3, "gpu": 1}
