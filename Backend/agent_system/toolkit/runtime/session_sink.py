"""SessionEventSink: delivers a session's tool and view events in-process
(merged plan, Phase 1.4 step 2).

Tools used to report through ``POST /events`` from the MCP subprocess. Tools
now run in the Agent System process, so their events go straight to the
session: no HTTP, no internal token.

- A ``ToolEvent`` goes to the session's execution monitor when a run has one
  (it records the event and sends the ``tool_update`` frame; in Plan 10 the
  Node Runner takes this role). Without one, for instance a Main Agent call
  between runs, the same ``tool_update`` frame goes to the WebSocket manager.
- A ``ViewEvent`` goes to the WebSocket manager as a frame of its own type,
  as ``view_emitter.emit_view_update`` sends it today.

The session and the manager are passed in, never imported: the app owns the
one ``socket_store.manager``, and importing it from here could load a second
copy of that module (``socket_store`` vs ``agent_system.socket_store``) whose
manager has no sockets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..events import ToolEvent, ViewEvent

TOOL_UPDATE = "tool_update"


class ToolEventMonitor(Protocol):
    async def on_tool_event(self, event: Any) -> None: ...


class LiveSession(Protocol):
    """What the sink reads from ``models.session.Session``."""

    session_id: str
    execution_monitor: ToolEventMonitor | None  # set while a run is active


class WebSocketManager(Protocol):
    """What the sink uses of ``socket_store.ConnectionManager``."""

    async def send_ws_message(self, msg_type: str, payload: Any, session_id: str = ...) -> None: ...


@dataclass(frozen=True)
class SessionEventSink:
    session: LiveSession
    manager: WebSocketManager

    async def emit(self, event: ToolEvent) -> None:
        if str(event.session_id) != str(self.session.session_id):
            # Every socket only ever receives its own session's frames; keep it so.
            raise ValueError(
                f"event for session {event.session_id!r} sent to the sink of "
                f"session {self.session.session_id!r}"
            )
        # Read at emit time: a run attaches its monitor after the sink exists.
        monitor = self.session.execution_monitor
        if monitor is not None:
            await monitor.on_tool_event(event)
        else:
            await self._send(TOOL_UPDATE, event.model_dump(mode="json"))

    async def emit_view(self, event: ViewEvent) -> None:
        await self._send(event.type, dict(event.payload))

    async def _send(self, msg_type: str, payload: dict[str, Any]) -> None:
        await self.manager.send_ws_message(msg_type, payload, session_id=self.session.session_id)
