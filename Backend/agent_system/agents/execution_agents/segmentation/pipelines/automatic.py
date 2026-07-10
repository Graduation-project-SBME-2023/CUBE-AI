import logging

from agent_system.models.tool_logging import ProgressPayload, TextPayload
from ..logging_config import emit_tool_event, emit_tool_events
from .common import raise_for_segmentation_error

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

    failures = []
    for index, volume_uid in enumerate(volumes, start=1):
        logger.info("[%s/%s] Running segmentation for volume: %s", index, len(volumes), volume_uid)
        await emit_tool_event(
            event_type="tool_progress",
            content=ProgressPayload(current=index, total=len(volumes), label="Segmenting volumes"),
            content_type="progress",
            session_id=session_id,
        )

        try:
            for label_index, label in enumerate(labels):
                model = _model_for_label(selected_auto_models, label_index, label)
                result = await segmentation_service.run_segmentation(
                    model=model,
                    series_instance_uid=volume_uid,
                    label_info=label,
                )
                raise_for_segmentation_error(result, volume_uid, label)
            logger.info("Completed segmentation for volume: %s", volume_uid)
        except Exception as exc:
            failures.append((volume_uid, str(exc)))
            logger.exception("Segmentation failed for %s", volume_uid)

    if failures:
        failed_uids = ", ".join(uid for uid, _ in failures)
        raise RuntimeError(f"Automatic segmentation failed for {len(failures)} volumes: {failed_uids}")
