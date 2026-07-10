import logging

from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from agent_system.models.tool_logging import ProgressPayload, TextPayload

from ..logging_config import emit_tool_event, emit_tool_events
from ..utils import check_voxtell_label_availability
from .common import raise_for_segmentation_error

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


def _supported_voxtell_labels(labels):
    supported = []
    unsupported = []
    for label in labels:
        canonical_label = check_voxtell_label_availability(label)
        if canonical_label:
            supported.append(canonical_label)
        else:
            unsupported.append(label)
    return supported, unsupported


async def run_interactive_pipeline(
    user_fallback_agent,
    user_proxy,
    volumes,
    labels,
    segmentation_service,
    session_id,
):
    payload = TextPayload(message=f"Starting interactive segmentation on {len(volumes)} volumes.")
    await emit_tool_events(
        event_types=["tool_started", "tool_progress"],
        content=payload,
        session_id=session_id,
    )

    selected_model = None
    logger.info("Starting interactive segmentation guidance for labels: %s", labels)
    supported_labels, unsupported_labels = _supported_voxtell_labels(labels)
    if unsupported_labels:
        logger.warning(
            "VoxTell does not support these labels and they require manual segmentation: %s",
            unsupported_labels,
        )
    if not supported_labels:
        raise RuntimeError(f"No requested labels are supported by VoxTell: {labels}")

    failures = []
    for index, volume_uid in enumerate(volumes, start=1):
        logger.info(
            "[%s/%s] Guiding user through interactive segmentation for volume: %s",
            index,
            len(volumes),
            volume_uid,
        )
        await emit_tool_event(
            event_type="tool_progress",
            content=ProgressPayload(current=index, total=len(volumes), label="Segmenting volumes"),
            content_type="progress",
            session_id=session_id,
        )

        try:
            for canonical_label in supported_labels:
                selected_model = "voxtell"
                result = await segmentation_service.run_segmentation(
                    model=selected_model,
                    series_instance_uid=volume_uid,
                    label_info=canonical_label,
                    params={"text_prompts": [canonical_label]},
                )
                raise_for_segmentation_error(result, volume_uid, canonical_label)
                logger.info(
                    "Completed interactive segmentation for volume %s with label %s",
                    volume_uid,
                    canonical_label,
                )
        except Exception as exc:
            failures.append((volume_uid, str(exc)))
            logger.exception("Interactive segmentation failed for %s", volume_uid)

    if failures:
        failed_uids = ", ".join(uid for uid, _ in failures)
        raise RuntimeError(f"Interactive segmentation failed for {len(failures)} volumes: {failed_uids}")

    return selected_model
