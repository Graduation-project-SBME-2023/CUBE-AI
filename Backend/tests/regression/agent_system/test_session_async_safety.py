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

        with patch.object(socket_store.BackendClient, "save_log", fake_save):
            payload = {"value": 1}
            await manager.send_ws_message(
                "tool_progress", payload, session_id="session-1"
            )
            payload["value"] = 99

        self.assertEqual(published["payload"]["value"], 1)

    async def test_tool_view_payload_is_snapshotted_before_it_is_published(self):
        manager = ConnectionManager()
        published = {}

        async def fake_save(log):
            published["payload"] = log.payload
            return SimpleNamespace(event_id=8, payload=log.payload)

        with patch.object(socket_store.BackendClient, "save_log", fake_save):
            payload = {"items": ["original"]}
            await manager.send_ws_message("tool_view", payload, session_id="session-1")
            payload["items"].append("mutated")

        self.assertEqual(published["payload"]["items"], ["original"])

    async def test_a_failed_persist_still_delivers_the_event_without_a_cursor(self):
        """Dropping it would hide work that really happened; pretending it has
        an event_id would make replay skip it."""
        manager = ConnectionManager()
        sent = []

        class _Socket:
            async def send_text(self, message):
                sent.append(json.loads(message))

        manager.active_connections.append(_Socket())

        async def failing_save(_log):
            raise RuntimeError("application api unavailable")

        with patch.object(socket_store.BackendClient, "save_log", failing_save):
            await manager.send_ws_message("tool_progress", {"v": 1}, session_id="s1")

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
        socket = object()
        manager.active_connections.append(socket)
        await manager.push_to_queue("review response", "session-a")

        manager.disconnect(socket)

        self.assertEqual(
            await manager.wait_for_user_message("session-a"),
            "review response",
        )


if __name__ == "__main__":
    unittest.main()
