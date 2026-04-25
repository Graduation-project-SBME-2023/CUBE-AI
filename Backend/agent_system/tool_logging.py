from models.tool_logging import ToolEvent


async def receive_tool_event(event: ToolEvent):
    # send instead via pipeline monitor???
    from app import session_service
    session = await session_service.async_require_session(event.session_id)
    await session.execution_monitor.on_tool_event(event)
