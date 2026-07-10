import logging
from pathlib import Path

from agent_system.models.tool_logging import ToolEvent
from agent_system.tool_logging import receive_tool_event


AGENT_SOURCE = "segmentation_agent"


def setup_logging():
    log_dir = Path(__file__).parent.parent.parent / "memory" / "workspace"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "segmentation_module.log"

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if not any(getattr(handler, "_segmentation_console", False) for handler in root_logger.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler._segmentation_console = True
        root_logger.addHandler(console_handler)

    if not any(getattr(handler, "_segmentation_file", False) for handler in root_logger.handlers):
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        file_handler._segmentation_file = True
        root_logger.addHandler(file_handler)


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
