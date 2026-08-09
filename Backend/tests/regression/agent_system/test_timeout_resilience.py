import asyncio
import unittest
from unittest.mock import patch

import httpx

from agent_system.agents.execution_agents.segmentation.pipelines.common import (
    ToolDeadlineExceeded,
    run_with_progress_heartbeat,
)
from agent_system.agents.execution_agents.segmentation.services import (
    SegmentationService,
)
from agent_system.clients.backend_client import DEFAULT_TIMEOUT
from agent_system.services.best_effort import (
    drain_best_effort,
    initialize_best_effort,
    schedule_best_effort,
)


class LongToolDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_wall_clock_deadline_cancels_inner_tool(self):
        cancelled = asyncio.Event()

        async def never_finishes():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with self.assertRaises(ToolDeadlineExceeded):
            await run_with_progress_heartbeat(
                never_finishes(),
                session_id=None,
                current=0.5,
                total=1,
                label="Long test tool",
                volume_uid="volume",
                interval_seconds=1.0,
                timeout_seconds=0.02,
            )

        self.assertTrue(cancelled.is_set())


class BestEffortPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await drain_best_effort()

    async def test_scheduling_does_not_wait_for_control_plane_write(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_write():
            started.set()
            await release.wait()

        scheduled = schedule_best_effort(
            slow_write,
            name="test-write",
            timeout_seconds=1.0,
        )
        self.assertTrue(scheduled)
        await started.wait()
        self.assertFalse(release.is_set())
        release.set()

    def test_control_plane_timeout_is_short(self):
        self.assertEqual(DEFAULT_TIMEOUT.connect, 3.0)
        self.assertEqual(DEFAULT_TIMEOUT.read, 8.0)


class BestEffortLoopLifecycleTests(unittest.TestCase):
    def test_separate_event_loops_have_separate_registries(self):
        async def run_cycle():
            completed = asyncio.Event()

            async def write():
                completed.set()

            initialize_best_effort()
            self.assertTrue(schedule_best_effort(write, name="loop-write"))
            await drain_best_effort()
            self.assertTrue(completed.is_set())

        asyncio.run(run_cycle())
        asyncio.run(run_cycle())


class DurableInferenceClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_starts_polls_and_downloads_durable_result(self):
        calls = {"post": 0, "status": 0, "result": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                calls["post"] += 1
                return httpx.Response(202, json={"status": "QUEUED"})
            if request.url.path.endswith("/result"):
                calls["result"] += 1
                return httpx.Response(
                    200,
                    headers={"content-type": "multipart/form-data; boundary=test"},
                    content=b"durable-result",
                )
            calls["status"] += 1
            status = "RUNNING" if calls["status"] == 1 else "SUCCEEDED"
            return httpx.Response(
                200,
                json={"job_id": "a" * 64, "status": status, "attempt": 1},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = SegmentationService(monai_url="http://monai", client=client)
        try:
            with patch(
                "agent_system.agents.execution_agents.segmentation.services.SEGMENTATION_POLL_SECONDS",
                0.001,
            ):
                response, job = await service._await_inference_job(
                    job_id="a" * 64,
                    model="model",
                    series_instance_uid="series",
                    params={"text_prompts": ["liver"]},
                )
        finally:
            await client.aclose()

        self.assertEqual(response.content, b"durable-result")
        self.assertEqual(job["status"], "SUCCEEDED")
        self.assertEqual(calls, {"post": 1, "status": 2, "result": 1})

    async def test_cancelling_wait_propagates_to_remote_job(self):
        status_seen = asyncio.Event()
        delete_seen = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(202, json={"status": "QUEUED"})
            if request.method == "DELETE":
                delete_seen.set()
                return httpx.Response(200, json={"status": "CANCELLING"})
            status_seen.set()
            return httpx.Response(200, json={"status": "RUNNING"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = SegmentationService(monai_url="http://monai", client=client)
        with patch(
            "agent_system.agents.execution_agents.segmentation.services.SEGMENTATION_POLL_SECONDS",
            10.0,
        ):
            task = asyncio.create_task(
                service._await_inference_job(
                    job_id="b" * 64,
                    model="model",
                    series_instance_uid="series",
                    params={},
                )
            )
            await status_seen.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        await client.aclose()
        self.assertTrue(delete_seen.is_set())


if __name__ == "__main__":
    unittest.main()
