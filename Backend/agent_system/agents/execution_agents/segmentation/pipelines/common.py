import asyncio
import logging
import time

from agent_system.models.tool_logging import ProgressPayload

from ..logging_config import emit_tool_event

logger = logging.getLogger(__name__)


class SegmentationPipelineError(RuntimeError):
    def __init__(
        self,
        *,
        volume_uid: str,
        label: str,
        stage: str,
        message: str,
        details: dict | None = None,
    ):
        self.volume_uid = volume_uid
        self.label = label
        self.stage = stage
        self.detail = message
        self.details = details or {}
        super().__init__(
            f"Segmentation failed for volume {volume_uid}, label {label}, "
            f"stage {stage}: {message}"
        )

    def as_dict(self) -> dict:
        result = {
            "volume_uid": self.volume_uid,
            "label": self.label,
            "stage": self.stage,
            "message": self.detail,
        }
        result.update(self.details)
        return result


def raise_for_segmentation_error(result, volume_uid: str, label: str) -> None:
    if isinstance(result, dict) and (result.get("error") or result.get("upload_error")):
        stage = result.get("stage") or "upload"
        message = result.get("message") or result.get("upload_response") or "Unknown segmentation error"
        raise SegmentationPipelineError(
            volume_uid=volume_uid,
            label=label,
            stage=str(stage),
            message=str(message),
            details={
                key: result[key]
                for key in (
                    "model",
                    "prompt",
                    "retry_count",
                    "status_code",
                    "upload_status_code",
                )
                if result.get(key) is not None
            },
        )


def check_session_cancelled(session_id: str | None) -> None:
    if not session_id:
        return
    try:
        from app import session_service
        from models.session import SessionState
        session = session_service.store.get(session_id)
        if session and (session.cancel_event.is_set() or session.state in (SessionState.CANCELLED, SessionState.FAILED)):
            logger.warning("Session %s is %s; halting segmentation pipeline.", session_id, session.state.name)
            raise asyncio.CancelledError(f"Session {session_id} execution halted ({session.state.name}).")
    except (KeyError, ImportError):
        pass


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
    check_session_cancelled(session_id)
    task = asyncio.create_task(awaitable)
    started = time.monotonic()
    last_log_time = started

    try:
        while True:
            check_session_cancelled(session_id)
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
            except asyncio.TimeoutError:
                check_session_cancelled(session_id)
                now = time.monotonic()
                if now - last_log_time >= interval_seconds:
                    last_log_time = now
                    elapsed = int(now - started)
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
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        raise
