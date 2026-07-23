import asyncio
import logging
import time

from agent_system.models.tool_logging import ProgressPayload

from ..logging_config import emit_tool_event

logger = logging.getLogger(__name__)


def raise_for_segmentation_error(result, volume_uid: str, label: str) -> None:
    if isinstance(result, dict) and (result.get("error") or result.get("upload_error")):
        stage = result.get("stage") or "upload"
        message = result.get("message") or result.get("upload_response") or "Unknown segmentation error"
        raise RuntimeError(
            f"Segmentation failed for volume {volume_uid}, label {label}, stage {stage}: {message}"
        )


async def run_with_progress_heartbeat(
    awaitable,
    *,
    session_id: str | None,
    current: float,
    total: int,
    label: str,
    volume_uid: str,
    interval_seconds: float = 30.0,
):
    task = asyncio.create_task(awaitable)
    started = time.monotonic()

    try:
        while True:
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=interval_seconds)
            except asyncio.TimeoutError:
                elapsed = int(time.monotonic() - started)
                short_uid = volume_uid[-8:] if volume_uid else "unknown"
                logger.info("%s (%ds elapsed, UID ...%s)", label, elapsed, short_uid)
                await emit_tool_event(
                    event_type="tool_progress",
                    content=ProgressPayload(
                        current=current,
                        total=total,
                        label=label,
                    ),
                    content_type="progress",
                    session_id=session_id,
                )
    except asyncio.CancelledError:
        task.cancel()
        raise
