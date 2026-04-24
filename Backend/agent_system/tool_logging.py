from models.session import SessionState
from socket_store import  send_ws_message
from models.tool_logging import ToolEvent
from app import session_service


async def receive_tool_event(event: ToolEvent):
    # send instead via pipeline monitor???
    session = session_service.get_session(event.session_id)
    session.execution_monitor.on_tool_event(event)
