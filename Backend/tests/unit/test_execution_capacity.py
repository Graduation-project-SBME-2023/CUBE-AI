import asyncio
import unittest

from services.execution_capacity import ExecutionCapacity


class ExecutionCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_capacity_is_bounded_and_fifo(self):
        capacity = ExecutionCapacity(max_concurrency=1, queue_capacity=2)

        first = await capacity.reserve("session-a")
        second = await capacity.reserve("session-b")
        third = await capacity.reserve("session-c")
        rejected = await capacity.reserve("session-d")

        self.assertTrue(first.accepted and not first.queued)
        self.assertTrue(second.accepted and second.queue_position == 1)
        self.assertTrue(third.accepted and third.queue_position == 2)
        self.assertFalse(rejected.accepted)

        second_turn = asyncio.create_task(capacity.wait_for_turn("session-b"))
        third_turn = asyncio.create_task(capacity.wait_for_turn("session-c"))
        await capacity.release("session-a")
        await asyncio.wait_for(second_turn, timeout=1)
        self.assertFalse(third_turn.done())

        await capacity.release("session-b")
        await asyncio.wait_for(third_turn, timeout=1)
        await capacity.release("session-c")

    async def test_cancelling_queued_session_releases_its_reservation(self):
        capacity = ExecutionCapacity(max_concurrency=1, queue_capacity=1)
        await capacity.reserve("session-a")
        await capacity.reserve("session-b")

        queued_wait = asyncio.create_task(capacity.wait_for_turn("session-b"))
        await capacity.release("session-b")
        with self.assertRaises(asyncio.CancelledError):
            await queued_wait

        replacement = await capacity.reserve("session-c")
        self.assertTrue(replacement.accepted and replacement.queued)
        await capacity.release("session-a")
        await capacity.wait_for_turn("session-c")
        await capacity.release("session-c")


if __name__ == "__main__":
    unittest.main()
