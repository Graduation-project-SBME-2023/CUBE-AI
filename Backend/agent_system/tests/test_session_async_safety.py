import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from clients.backend_client import BackendClient
from services.session_service import SessionService
from socket_store import ConnectionManager


class DeferredPersistenceSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_log_payload_is_snapshotted_before_scheduling(self):
        operations = []
        ws = SimpleNamespace(send_ws_message=AsyncMock())
        service = SessionService(ws)
        session = SimpleNamespace(
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
