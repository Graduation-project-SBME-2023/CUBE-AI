import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from clients.backend_client import BackendClient
from services.session_service import SessionService
from socket_store import ConnectionManager, WebSocketCapacityExceeded


class FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.sent: list[str] = []
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def send_text(self, message: str):
        self.sent.append(message)

    async def close(self, **_kwargs):
        self.closed = True


class DeferredPersistenceSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_log_payload_is_snapshotted_before_scheduling(self):
        operations = []
        ws = SimpleNamespace(send_ws_message=AsyncMock())
        service = SessionService(ws)
        session = SimpleNamespace(
            session_id="session-1",
            plan=SimpleNamespace(
                steps=[SimpleNamespace(module="agent", id="step-1")]
            )
        )
        log = {
            "type": "tool_progress",
            "payload": {"value": 1},
            "session_id": "session-1",
        }

        def capture(operation, **_kwargs):
            operations.append(operation)
            return True

        with (
            patch("services.session_service.schedule_best_effort", capture),
            patch.object(
                BackendClient,
                "update_plan_step",
                new=AsyncMock(),
            ) as update,
        ):
            await service.update_agent_tool_logs(session, "agent", log)
            log["payload"]["value"] = 99
            await operations[0]()

        persisted = update.await_args.args[1]
        self.assertEqual(persisted["log"]["payload"]["value"], 1)

    async def test_tool_view_payload_is_snapshotted_before_scheduling(self):
        operations = []
        ws = SimpleNamespace(send_ws_message=AsyncMock())
        service = SessionService(ws)
        session = SimpleNamespace(
            session_id="session-1",
            plan=SimpleNamespace(
                steps=[SimpleNamespace(module="agent", id="step-1")]
            ),
        )
        payload = {"items": ["original"]}

        def capture(operation, **_kwargs):
            operations.append(operation)
            return True

        with (
            patch("services.session_service.schedule_best_effort", capture),
            patch.object(
                BackendClient,
                "update_plan_step",
                new=AsyncMock(),
            ) as update,
        ):
            await service.update_tool_views(
                session,
                "agent",
                "tool_view",
                payload,
            )
            payload["items"].append("mutated")
            await operations[0]()

        persisted = update.await_args.args[1]
        self.assertEqual(
            persisted["log"]["payload"]["items"],
            ["original"],
        )


class SessionQueueLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_view_output_is_persisted_before_send_returns(self):
        manager = ConnectionManager()

        with (
            patch("socket_store.schedule_best_effort") as schedule,
            patch.object(
                BackendClient,
                "save_log",
                new=AsyncMock(side_effect=lambda log: log),
            ) as save_log,
        ):
            event = await manager.send_ws_message(
                "report_output",
                {"report": "complete"},
                "session-a",
            )

        schedule.assert_not_called()
        save_log.assert_awaited_once()
        persisted = save_log.await_args.args[0]
        self.assertEqual(persisted.uuid, event["event_id"])
        self.assertEqual(persisted.session_id, "session-a")

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
        socket = FakeWebSocket()
        await manager.connect(socket, "session-a")
        await manager.push_to_queue("review response", "session-a")

        manager.disconnect(socket)

        self.assertEqual(
            await manager.wait_for_user_message("session-a"),
            "review response",
        )

    async def test_outbound_events_are_isolated_by_session(self):
        manager = ConnectionManager()
        socket_a = FakeWebSocket()
        socket_b = FakeWebSocket()
        await manager.connect(socket_a, "session-a")
        await manager.connect(socket_b, "session-b")

        event = await manager.send_ws_message(
            "status",
            {"value": "a-only"},
            "session-a",
            replaying=True,
        )
        await manager._outbound_queues[socket_a].join()

        self.assertEqual(len(socket_a.sent), 1)
        self.assertEqual(socket_b.sent, [])
        self.assertEqual(json.loads(socket_a.sent[0])["session_id"], "session-a")
        self.assertEqual(event["version"], 1)
        self.assertEqual(event["sequence"], 1)
        self.assertTrue(event["event_id"])
        await manager.shutdown()

    async def test_multiple_tabs_receive_their_shared_session_events(self):
        manager = ConnectionManager()
        first_tab = FakeWebSocket()
        second_tab = FakeWebSocket()
        other_session = FakeWebSocket()
        await manager.connect(first_tab, "session-a")
        await manager.connect(second_tab, "session-a")
        await manager.connect(other_session, "session-b")

        await manager.send_ws_message("status", "ready", "session-a", replaying=True)
        await manager._outbound_queues[first_tab].join()
        await manager._outbound_queues[second_tab].join()

        self.assertEqual(len(first_tab.sent), 1)
        self.assertEqual(len(second_tab.sent), 1)
        self.assertEqual(other_session.sent, [])
        await manager.shutdown()

    async def test_connection_limits_cover_sessions_and_the_whole_process(self):
        manager = ConnectionManager()
        manager._max_connections = 2
        manager._max_connections_per_session = 1
        first = FakeWebSocket()
        second = FakeWebSocket()

        await manager.connect(first, "session-a")
        with self.assertRaises(WebSocketCapacityExceeded):
            await manager.connect(FakeWebSocket(), "session-a")

        await manager.connect(second, "session-b")
        with self.assertRaises(WebSocketCapacityExceeded):
            await manager.connect(FakeWebSocket(), "session-c")
        await manager.shutdown()

    async def test_session_id_is_required_for_input_and_output(self):
        manager = ConnectionManager()
        with self.assertRaises(ValueError):
            await manager.push_to_queue("unscoped")
        with self.assertRaises(ValueError):
            await manager.send_ws_message("status", "bad", "", replaying=True)

    async def test_input_queue_is_bounded_per_session(self):
        manager = ConnectionManager()
        manager._input_queue_size = 1

        self.assertTrue(await manager.push_to_queue("first", "session-a"))
        self.assertFalse(await manager.push_to_queue("second", "session-a"))
        self.assertTrue(await manager.push_to_queue("other", "session-b"))

    async def test_client_message_ids_are_deduplicated_per_session(self):
        manager = ConnectionManager()

        self.assertTrue(manager.remember_client_message("session-a", "message-1"))
        self.assertFalse(manager.remember_client_message("session-a", "message-1"))
        self.assertTrue(manager.remember_client_message("session-b", "message-1"))

        manager.forget_client_message("session-a", "message-1")
        self.assertTrue(manager.remember_client_message("session-a", "message-1"))


if __name__ == "__main__":
    unittest.main()
