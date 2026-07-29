from agent_system.models.tool_logging import ToolEvent
from agent_system.tool_logging import receive_tool_event


AGENT_SOURCE = "segmentation_agent"

async def emit_tool_event(
    *,
    event_type: str,
    content,
    session_id: str | None,
    content_type: str = "text",
    source: str = AGENT_SOURCE,
) -> None:
    await receive_tool_event(
        ToolEvent(
            event_type=event_type,
            source=source,
            content_type=content_type,
            content=content,
            session_id=session_id,
        )
    )


async def emit_tool_events(
    *,
    event_types: list[str],
    content,
    session_id: str | None,
    content_type: str = "text",
    source: str = AGENT_SOURCE,
) -> None:
    for event_type in event_types:
        await emit_tool_event(
            event_type=event_type,
            content=content,
            session_id=session_id,
            content_type=content_type,
            source=source,
        )
