import logging

from agent_system.models.tool_logging import ProgressPayload, TextPayload
from ..logging_config import emit_tool_event, emit_tool_events
from .common import raise_for_segmentation_error, run_with_progress_heartbeat

logger = logging.getLogger(__name__)


def _model_for_label(selected_models, label_index: int, label: str) -> str:
    if label_index < len(selected_models):
        return selected_models[label_index]
    if len(selected_models) == 1:
        return selected_models[0]
    raise ValueError(f"No automatic segmentation model selected for label '{label}'.")


async def run_automatic_pipeline(
    volumes,
    labels,
    selected_auto_models,
    segmentation_service,
    session_id
):
    payload = TextPayload(message=f"Starting automatic segmentation on {len(volumes)} volumes.")
    await emit_tool_events(
        event_types=["tool_started", "tool_progress"],
        content=payload,
        session_id=session_id,
    )
    logger.info("Starting automatic segmentation on %s volumes", len(volumes))

    if not selected_auto_models:
        raise ValueError("Automatic segmentation requested without a selected model.")

    # Emit initial progress (0 completed) before any volume segmentation starts
    await emit_tool_event(
        event_type="tool_progress",
        content=ProgressPayload(current=0, total=len(volumes), label="Segmenting volumes"),
        content_type="progress",
        session_id=session_id,
    )

    failures = []
    for index, volume_uid in enumerate(volumes, start=1):
        logger.info("[%s/%s] Running segmentation for volume: %s", index, len(volumes), volume_uid)
        # Emit in-progress step (current = index - 0.5) so progress bar animates between index-1 and index
        await emit_tool_event(
            event_type="tool_progress",
            content=ProgressPayload(
                current=index - 0.5,
                total=len(volumes),
                label="Segmenting volumes"
            ),
            content_type="progress",
            session_id=session_id,
        )

        try:
            for label_index, label in enumerate(labels):
                model = _model_for_label(selected_auto_models, label_index, label)
                result = await run_with_progress_heartbeat(
                    segmentation_service.run_segmentation(
                        model=model,
                        series_instance_uid=volume_uid,
                        label_info=label,
                    ),
                    session_id=session_id,
                    current=index - 0.5,
                    total=len(volumes),
                    label=f"Segmenting volume {index} of {len(volumes)}",
                    volume_uid=volume_uid,
                )
                raise_for_segmentation_error(result, volume_uid, label)
            logger.info("Completed segmentation for volume: %s", volume_uid)
            # Emit completed progress step (current = index)
            await emit_tool_event(
                event_type="tool_progress",
                content=ProgressPayload(
                    current=index,
                    total=len(volumes),
                    label="Segmenting volumes"
                ),
                content_type="progress",
                session_id=session_id,
            )
        except Exception as exc:
            failures.append((volume_uid, str(exc)))
            logger.exception("Segmentation failed for %s", volume_uid)
            await emit_tool_event(
                event_type="tool_progress",
                content=ProgressPayload(
                    current=index,
                    total=len(volumes),
                    label="Segmenting volumes"
                ),
                content_type="progress",
                session_id=session_id,
            )

    if failures:
        # Log failures as warnings but do NOT raise — partial success is still useful.
        # A single OOM-killed volume (exit 137) must not abort the entire pipeline.
        failed_uids = ", ".join(uid for uid, _ in failures)
        logger.warning(
            "Automatic segmentation partially failed: %d/%d volumes could not be processed. "
            "Failed UIDs: %s. Downstream download/GIF steps will proceed for successful volumes.",
            len(failures), len(volumes), failed_uids,
        )
        return {"partial_failure": True, "failed_uids": [uid for uid, _ in failures]}

    return {"partial_failure": False, "failed_uids": []}
