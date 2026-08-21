import asyncio
import unittest

from autogen_core import CancellationToken
from autogen_core.tools import BaseTool
from pydantic import BaseModel

from agents.execution_agents.segmentation.pipelines.common import (
    resources_for_segmentation_item,
)
from services.resource_scheduler import ResourceScheduler
from services.resource_tools import ResourceGuardedTool


class _Args(BaseModel):
    value: int


class _FakeTool(BaseTool):
    def __init__(self, started: asyncio.Event):
        super().__init__(
            args_type=_Args,
            return_type=int,
            name="expensive_tool",
            description="Test tool",
        )
        self.started = started

    async def run(self, args: _Args, cancellation_token: CancellationToken) -> int:
        self.started.set()
        return args.value * 2


class ResourceSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_resource_can_bypass_blocked_gpu_waiter(self):
        scheduler = ResourceScheduler(
            {"gpu_segmentation": 1, "cpu_heavy": 1},
            max_waiters=4,
        )
        first_gpu = await scheduler.acquire(
            ("gpu_segmentation",),
            session_id="session-a",
            operation="segmentation-a",
            agent_id="segmentation_agent",
        )
        second_gpu_task = asyncio.create_task(
            scheduler.acquire(
                ("gpu_segmentation",),
                session_id="session-b",
                operation="segmentation-b",
                agent_id="segmentation_agent",
            )
        )
        await asyncio.sleep(0)

        cpu = await asyncio.wait_for(
            scheduler.acquire(
                ("cpu_heavy",),
                session_id="session-c",
                operation="features-c",
                agent_id="feature_extraction_agent",
            ),
            timeout=1,
        )
        self.assertFalse(second_gpu_task.done())

        await scheduler.release(cpu)
        await scheduler.release(first_gpu)
        second_gpu = await asyncio.wait_for(second_gpu_task, timeout=1)
        await scheduler.release(second_gpu)

    async def test_compound_request_is_atomic_and_not_bypassed(self):
        scheduler = ResourceScheduler(
            {"gpu_segmentation": 1, "cpu_heavy": 1},
            max_waiters=4,
        )
        cpu = await scheduler.acquire(
            ("cpu_heavy",),
            session_id="session-a",
            operation="features-a",
            agent_id="feature_extraction_agent",
        )
        compound_task = asyncio.create_task(
            scheduler.acquire(
                ("cpu_heavy", "gpu_segmentation"),
                session_id="session-b",
                operation="voxtell-cache-miss",
                agent_id="segmentation_agent",
            )
        )
        await asyncio.sleep(0)
        later_gpu_task = asyncio.create_task(
            scheduler.acquire(
                ("gpu_segmentation",),
                session_id="session-c",
                operation="segmentation-c",
                agent_id="segmentation_agent",
            )
        )
        await asyncio.sleep(0)

        self.assertFalse(compound_task.done())
        self.assertFalse(later_gpu_task.done())
        await scheduler.release(cpu)

        compound = await asyncio.wait_for(compound_task, timeout=1)
        self.assertFalse(later_gpu_task.done())
        status = await scheduler.status()
        self.assertEqual(status["resources"]["cpu_heavy"]["in_use"], 1)
        self.assertEqual(status["resources"]["gpu_segmentation"]["in_use"], 1)

        await scheduler.release(compound)
        later_gpu = await asyncio.wait_for(later_gpu_task, timeout=1)
        await scheduler.release(later_gpu)

    async def test_cancelled_waiter_is_removed(self):
        scheduler = ResourceScheduler({"gpu_segmentation": 1}, max_waiters=2)
        first = await scheduler.acquire(
            ("gpu_segmentation",),
            session_id="session-a",
            operation="segmentation-a",
            agent_id="segmentation_agent",
        )
        waiting = asyncio.create_task(
            scheduler.acquire(
                ("gpu_segmentation",),
                session_id="session-b",
                operation="segmentation-b",
                agent_id="segmentation_agent",
            )
        )
        await asyncio.sleep(0)
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting

        await scheduler.release(first)
        status = await scheduler.status()
        self.assertEqual(status["total_waiting"], 0)
        self.assertEqual(status["resources"]["gpu_segmentation"]["in_use"], 0)

    async def test_cancelled_promoted_waiter_releases_resource(self):
        scheduler = ResourceScheduler({"gpu_segmentation": 1}, max_waiters=2)
        first = await scheduler.acquire(
            ("gpu_segmentation",),
            session_id="session-a",
            operation="segmentation-a",
            agent_id="segmentation_agent",
        )
        waiting = asyncio.create_task(
            scheduler.acquire(
                ("gpu_segmentation",),
                session_id="session-b",
                operation="segmentation-b",
                agent_id="segmentation_agent",
            )
        )
        await asyncio.sleep(0)

        await scheduler.release(first)
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting

        status = await scheduler.status()
        self.assertEqual(status["total_waiting"], 0)
        self.assertEqual(status["resources"]["gpu_segmentation"]["in_use"], 0)
        self.assertEqual(status["active"], [])

    async def test_guarded_tool_waits_for_resource(self):
        scheduler = ResourceScheduler({"cpu_heavy": 1}, max_waiters=2)
        blocker = await scheduler.acquire(
            ("cpu_heavy",),
            session_id="session-a",
            operation="blocker",
            agent_id="feature_extraction_agent",
        )
        started = asyncio.Event()
        guarded = ResourceGuardedTool(
            _FakeTool(started),
            resources=("cpu_heavy",),
            session_id="session-b",
            agent_id="modeling_agent",
            scheduler=scheduler,
        )
        run_task = asyncio.create_task(
            guarded.run_json({"value": 3}, CancellationToken())
        )
        await asyncio.sleep(0)
        self.assertFalse(started.is_set())

        await scheduler.release(blocker)
        self.assertEqual(await asyncio.wait_for(run_task, timeout=1), 6)
        self.assertTrue(started.is_set())

    def test_voxtell_cache_miss_requires_cpu_and_gpu(self):
        self.assertEqual(
            resources_for_segmentation_item(
                {"model": "voxtell", "cache_hit": False}
            ),
            ("gpu_segmentation", "cpu_heavy"),
        )
        self.assertEqual(
            resources_for_segmentation_item(
                {"model": "voxtell", "cache_hit": True}
            ),
            ("gpu_segmentation",),
        )


if __name__ == "__main__":
    unittest.main()
