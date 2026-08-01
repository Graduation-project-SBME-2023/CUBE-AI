import logging
from collections.abc import Awaitable, Callable
from typing import Any

from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from agent_system.models.tool_logging import ProgressPayload, TextPayload

from ..logging_config import emit_tool_event, emit_tool_events
from .common import (
    SegmentationPipelineError,
    check_session_cancelled,
    raise_for_segmentation_error,
    run_with_progress_heartbeat,
)

logger = logging.getLogger(__name__)

PairHook = Callable[[dict[str, Any]], Awaitable[None]]


def create_interactive_agent(model_client, discovery_tools):
    return AssistantAgent(
        name="UserFallbackAgent",
        model_client=model_client,
        tools=discovery_tools,
        system_message="""
            You are UserFallbackAgent.

            Your role:
            - Explain available interactive segmentation models.
            - Explain their parameters using discovery tools.
            - Guide the user step-by-step.
            - Answer user questions.

            Rules:
            - You MUST NOT run segmentation or check masks.
            - You MUST wait for user confirmation via UserProxy.
            - Terminate only when the user confirms the task is done.
            """,
        reflect_on_tool_use=True,
        max_tool_iterations=3,
    )


def create_user_proxy_agent(model_client):
    return UserProxyAgent(name="HumanUser")


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


async def run_interactive_pipeline(
    user_fallback_agent,
    user_proxy,
    pending_work_items: list[dict[str, Any]],
    segmentation_service,
    session_id,
    *,
    on_pair_started: PairHook | None = None,
    on_pair_completed: PairHook | None = None,
):
    """Run interactive-path inference once per volume-target pair."""
    total = len(pending_work_items)
    payload = TextPayload(message=f"Starting interactive segmentation on {total} pairs.")
    await emit_tool_events(
        event_types=["tool_started", "tool_progress"],
        content=payload,
        session_id=session_id,
    )

    await emit_tool_event(
        event_type="tool_progress",
        content=ProgressPayload(current=0, total=total, label="Segmenting pairs"),
        content_type="progress",
        session_id=session_id,
    )

    outcomes: list[dict[str, Any]] = []
    for index, item in enumerate(pending_work_items, start=1):
        check_session_cancelled(session_id)
        pair_id = str(item["pair_id"])
        volume_uid = str(item["volume_uid"])
        label = str(item["canonical_label"])
        model = str(item.get("model") or "voxtell")
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
            logger.exception("Interactive segmentation failed for pair %s", pair_id)
            failure = exc.as_dict()
            failure["pair_id"] = pair_id
            outcome = {
                "pair_id": pair_id,
                "status": "failed",
                "failure": failure,
            }
        except Exception as exc:
            logger.exception("Interactive segmentation failed for pair %s", pair_id)
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
        "selected_model": pending_work_items[0].get("model") if pending_work_items else None,
        "failed_pair_ids": [failure["pair_id"] for failure in failures],
        "failures": failures,
        "results": results,
        "outcomes": outcomes,
    }
