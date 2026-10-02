import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import socket_store
from services.session_service import SessionService
from socket_store import ConnectionManager


class ToolLogSnapshotTests(unittest.IsolatedAsyncioTestCase):
    """A published event must be a snapshot, not a live reference."""

    async def test_tool_log_payload_is_snapshotted_before_it_is_published(self):
        manager = ConnectionManager()
        published = {}

        async def fake_save(log):
            published["payload"] = log.payload
            return SimpleNamespace(event_id=7, payload=log.payload)

        with patch.object(socket_store.BackendClient, "save_event", fake_save):
            payload = {"value": 1}
            # A durable type: "tool_progress" is registered ephemeral
            # (WS-05) and would skip persistence entirely, which is not what
            # this test is about.
            await manager.send_ws_message(
                "hypothesis", payload, session_id="session-1"
            )
            payload["value"] = 99

        self.assertEqual(published["payload"]["value"], 1)

    async def test_tool_view_payload_is_snapshotted_before_it_is_published(self):
        manager = ConnectionManager()
        published = {}

        async def fake_save(log):
            published["payload"] = log.payload
            return SimpleNamespace(event_id=8, payload=log.payload)

        with patch.object(socket_store.BackendClient, "save_event", fake_save):
            payload = {"items": ["original"]}
            # Unregistered types default to durable (event_registry.py's fail
            # -safe default), so this still exercises the persist path.
            await manager.send_ws_message("tool_view", payload, session_id="session-1")
            payload["items"].append("mutated")

        self.assertEqual(published["payload"]["items"], ["original"])

    async def test_a_failed_persist_still_delivers_the_event_without_a_cursor(self):
        """Dropping it would hide work that really happened; pretending it has
        an event_id would make replay skip it."""
        manager = ConnectionManager()
        sent = []
        received = asyncio.Event()

        class _Socket:
            async def accept(self):
                pass

            async def send_text(self, message):
                sent.append(json.loads(message))
                received.set()

        # Delivery is routed by session_id now (WS-02): the socket must be
        # bound to the same session the event is sent to, or it never sees
        # it. A durable type: "tool_progress" is ephemeral (WS-05) and would
        # never attempt to persist at all, which is not what this tests.
        await manager.connect(_Socket(), "s1")

        async def failing_save(_log):
            raise RuntimeError("application api unavailable")

        with patch.object(socket_store.BackendClient, "save_event", failing_save):
            await manager.send_ws_message("hypothesis", {"v": 1}, session_id="s1")
            await asyncio.wait_for(received.wait(), timeout=1.0)

        self.assertEqual(len(sent), 1)
        self.assertIsNone(sent[0]["event_id"])


class SessionQueueLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_session_reset_discards_only_that_queue(self):
        manager = ConnectionManager()
        await manager.push_to_queue("old-a", "session-a")
        await manager.push_to_queue("keep-b", "session-b")

        discarded = manager.clear_message_queue("session-a")

        self.assertEqual(discarded, 1)
        self.assertNotIn("session-a", manager.message_queues)
        self.assertEqual(
            await manager.wait_for_user_message("session-b"),
            "keep-b",
        )

    async def test_socket_disconnect_does_not_drop_valid_session_input(self):
        manager = ConnectionManager()

        class _Socket:
            async def accept(self):
                pass

        socket = _Socket()
        await manager.connect(socket, "session-a")
        await manager.push_to_queue("review response", "session-a")

        manager.disconnect(socket)

        self.assertEqual(
            await manager.wait_for_user_message("session-a"),
            "review response",
        )


if __name__ == "__main__":
    unittest.main()
