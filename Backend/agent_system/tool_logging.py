from models.tool_logging import ToolEvent


async def receive_tool_event(event: ToolEvent):
    # send instead via pipeline monitor???
    from app import session_service
    session = session_service.get_session(event.session_id)
    session.execution_monitor.on_tool_event(event)
