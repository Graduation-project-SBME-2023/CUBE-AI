import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent_system.models.tool_logging import ProgressPayload, TextPayload

from ..logging_config import emit_tool_event, emit_tool_events
from .common import (
    SegmentationPipelineError,
    raise_for_segmentation_error,
    run_with_progress_heartbeat,
)

logger = logging.getLogger(__name__)

PairHook = Callable[[dict[str, Any]], Awaitable[None]]


async def _notify_pair_hook(
    hook: PairHook | None,
    payload: dict[str, Any],
    work_item: dict[str, Any],
    *,
    hook_name: str,
) -> None:
    if hook is None:
        return
    try:
        await hook(payload)
    except Exception as exc:
        pair_id = str(work_item.get("pair_id") or "unknown")
        logger.exception("%s failed for pair %s", hook_name, pair_id)
        work_item.update(
            {
                "status": "failed",
                "error_stage": "preview_callback",
                "error": str(exc),
                "error_details": {"exception_type": type(exc).__name__},
            }
        )


async def run_automatic_pipeline(
    pending_work_items: list[dict[str, Any]],
    segmentation_service,
    session_id,
    *,
    on_pair_started: PairHook | None = None,
    on_pair_completed: PairHook | None = None,
):
    """Run automatic inference once per volume-target pair.

    Model, clinical label, and prompt are read from each work item so multiple
    targets for the same volume cannot be collapsed or positionally mismatched.
    """
    total = len(pending_work_items)
    payload = TextPayload(message=f"Starting automatic segmentation on {total} pairs.")
    await emit_tool_events(
        event_types=["tool_started", "tool_progress"],
        content=payload,
        session_id=session_id,
    )
    logger.info("Starting automatic segmentation on %s pairs", total)

    await emit_tool_event(
        event_type="tool_progress",
        content=ProgressPayload(current=0, total=total, label="Segmenting pairs"),
        content_type="progress",
        session_id=session_id,
    )

    outcomes: list[dict[str, Any]] = []
    for index, item in enumerate(pending_work_items, start=1):
        pair_id = str(item["pair_id"])
        volume_uid = str(item["volume_uid"])
        label = str(item["canonical_label"])
        model = str(item["model"])
        prompt = str(item.get("inference_prompt") or label)

        await _notify_pair_hook(
            on_pair_started,
            {"pair_id": pair_id, "status": "inferencing"},
            item,
            hook_name="Pair-start callback",
        )
        await emit_tool_event(
            event_type="tool_progress",
            content=ProgressPayload(
                current=index - 0.5,
                total=total,
                label="Segmenting pairs",
            ),
            content_type="progress",
            session_id=session_id,
        )

        try:
            result = await run_with_progress_heartbeat(
                segmentation_service.run_segmentation(
                    model=model,
                    series_instance_uid=volume_uid,
                    label_info=label,
                    params={"text_prompts": [prompt]},
                ),
                session_id=session_id,
                current=index - 0.5,
                total=total,
                label=f"Segmenting pair {index} of {total}",
                volume_uid=volume_uid,
            )
            raise_for_segmentation_error(result, volume_uid, label)
            outcome = {
                "pair_id": pair_id,
                "status": "succeeded",
                "inference_result": result,
            }
        except SegmentationPipelineError as exc:
            logger.exception("Segmentation failed for pair %s", pair_id)
            failure = exc.as_dict()
            failure["pair_id"] = pair_id
            outcome = {
                "pair_id": pair_id,
                "status": "failed",
                "failure": failure,
            }
        except Exception as exc:
            logger.exception("Segmentation failed for pair %s", pair_id)
            outcome = {
                "pair_id": pair_id,
                "status": "failed",
                "failure": {
                    "pair_id": pair_id,
                    "volume_uid": volume_uid,
                    "label": label,
                    "stage": "pipeline_exception",
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                },
            }

        outcomes.append(outcome)
        await _notify_pair_hook(
            on_pair_completed,
            outcome,
            item,
            hook_name="Pair-completion callback",
        )
        await emit_tool_event(
            event_type="tool_progress",
            content=ProgressPayload(current=index, total=total, label="Segmenting pairs"),
            content_type="progress",
            session_id=session_id,
        )

    failures = [outcome["failure"] for outcome in outcomes if outcome["status"] == "failed"]
    results = [
        {
            "pair_id": outcome["pair_id"],
            "result": outcome["inference_result"],
        }
        for outcome in outcomes
        if outcome["status"] == "succeeded"
    ]
    return {
        "partial_failure": bool(failures),
        "failed_pair_ids": [failure["pair_id"] for failure in failures],
        "failures": failures,
        "results": results,
        "outcomes": outcomes,
    }
