from socket_store import  send_ws_message
from models.tool_logging import ToolEvent


async def receive_tool_event(event: ToolEvent):
    await send_ws_message(
        msg_type="tool_update",
        payload=event.model_dump()
        # payload=event
    )