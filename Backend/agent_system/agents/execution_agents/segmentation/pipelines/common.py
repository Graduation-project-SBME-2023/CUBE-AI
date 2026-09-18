import asyncio
import logging
import time

from agent_system.models.tool_logging import ProgressPayload
from services.timeout_policy import (
    LONG_TOOL_TIMEOUT_SECONDS,
    TOOL_HEARTBEAT_SECONDS,
)

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


class SessionExecutionStateError(RuntimeError):
    """Raised when segmentation outlives a failed orchestration task."""


class ToolDeadlineExceeded(TimeoutError):
    """Raised when a long tool exceeds its configured wall-clock budget."""


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
        if not session:
            return

        if session.cancel_event.is_set():
            logger.info(
                "Session %s received an explicit stop signal; halting "
                "segmentation pipeline.",
                session_id,
            )
            raise asyncio.CancelledError(
                f"Session {session_id} execution stopped by explicit request."
            )

        if session.state == SessionState.FAILED:
            detail = session.last_error or "The parent execution task failed."
            logger.error(
                "Session %s failed while segmentation was active: %s",
                session_id,
                detail,
            )
            raise SessionExecutionStateError(
                f"Session {session_id} failed while segmentation was active: "
                f"{detail}"
            )

        if session.state == SessionState.CANCELLED:
            # cancel_event is the authoritative stop signal.  A still-running
            # execution can otherwise be poisoned by a stale CANCELLED state
            # left behind by an overlapping lifecycle update, which used to
            # abort segmentation even though nobody requested a stop.
            execution_task = session.execution_task
            if execution_task is not None and not execution_task.done():
                logger.warning(
                    "Session %s had a stale CANCELLED state while its execution "
                    "task was still active; restoring RUNNING.",
                    session_id,
                )
                session.state = SessionState.RUNNING
                return

            # With no live parent execution, the mismatch remains an internal
            # orchestration error rather than a user cancellation.
            message = (
                f"Session {session_id} is CANCELLED without an explicit "
                "stop signal."
            )
            logger.error(message)
            raise SessionExecutionStateError(message)
    except (KeyError, ImportError):
        pass


async def wait_if_session_paused(session_id: str | None) -> None:
    if not session_id:
        return
    try:
        from app import session_service

        session = session_service.store.get(session_id)
        if not session:
            return
        while not session.pause_event.is_set():
            check_session_cancelled(session_id)
            try:
                await asyncio.wait_for(session.pause_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
    except (KeyError, ImportError):
        return




async def run_with_progress_heartbeat(
    awaitable,
    *,
    session_id: str | None,
    current: float,
    total: int,
    label: str,
    volume_uid: str,
    interval_seconds: float = TOOL_HEARTBEAT_SECONDS,
    timeout_seconds: float = LONG_TOOL_TIMEOUT_SECONDS,
):
    check_session_cancelled(session_id)
    task = asyncio.create_task(awaitable)
    started = time.monotonic()
    wall_started = time.time()
    last_log_time = started

    try:
        while True:
            check_session_cancelled(session_id)
            await wait_if_session_paused(session_id)
            elapsed_seconds = time.monotonic() - started
            
            # Check for excessive sleep/suspension (e.g. host went to sleep for > 3 hours)
            wall_elapsed = time.time() - wall_started
            if wall_elapsed - elapsed_seconds > 3 * 3600:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise ToolDeadlineExceeded(
                    f"{label} aborted due to system suspension exceeding 3 hours"
                )

            remaining = timeout_seconds - elapsed_seconds
            if remaining <= 0:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise ToolDeadlineExceeded(
                    f"{label} exceeded its {timeout_seconds:.0f}s wall-clock limit"
                )
            try:
                return await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=min(3.0, remaining),
                )
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
