import logging

from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from agent_system.models.tool_logging import ProgressPayload, TextPayload

from ..logging_config import emit_tool_event, emit_tool_events
from .common import raise_for_segmentation_error, run_with_progress_heartbeat

logger = logging.getLogger(__name__)


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


async def run_interactive_pipeline(
    user_fallback_agent,
    user_proxy,
    volumes,
    labels,
    segmentation_service,
    session_id,
    inference_prompts=None,
):
    payload = TextPayload(message=f"Starting interactive segmentation on {len(volumes)} volumes.")
    await emit_tool_events(
        event_types=["tool_started", "tool_progress"],
        content=payload,
        session_id=session_id,
    )

    selected_model = None
    # VoxTell accepts ANY label — known labels use a validated prompt, unknown labels
    # use dynamic embedding generation. We pass all labels as-is.
    logger.info("Starting interactive segmentation for labels: %s (VoxTell accepts all)", labels)

    # Emit initial progress (0 completed) before any volume segmentation starts
    await emit_tool_event(
        event_type="tool_progress",
        content=ProgressPayload(current=0, total=len(volumes), label="Segmenting volumes"),
        content_type="progress",
        session_id=session_id,
    )

    failures = []
    for index, volume_uid in enumerate(volumes, start=1):
        logger.info(
            "[%s/%s] Guiding user through interactive segmentation for volume: %s",
            index,
            len(volumes),
            volume_uid,
        )
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
            for canonical_label in labels:
                selected_model = "voxtell"
                inference_prompt = (
                    (inference_prompts or {}).get(canonical_label)
                    or canonical_label
                )
                result = await run_with_progress_heartbeat(
                    segmentation_service.run_segmentation(
                        model=selected_model,
                        series_instance_uid=volume_uid,
                        label_info=canonical_label,
                        params={"text_prompts": [inference_prompt]},
                    ),
                    session_id=session_id,
                    current=index - 0.5,
                    total=len(volumes),
                    label=f"Segmenting volume {index} of {len(volumes)}",
                    volume_uid=volume_uid,
                )
                raise_for_segmentation_error(result, volume_uid, canonical_label)
                logger.info(
                    "Completed interactive segmentation for volume %s with label %s",
                    volume_uid,
                    canonical_label,
                )
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
            logger.exception("Interactive segmentation failed for %s", volume_uid)
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

    return selected_model, failures
