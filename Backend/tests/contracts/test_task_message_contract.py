from __future__ import annotations

import pytest

from models.plans import ContextAugmentation, TaskMessage


@pytest.mark.contract
def test_task_message_round_trip_preserves_agent_context():
    message = TaskMessage(
        task_id="task-1",
        phase="feature_extraction",
        agent_id="feature_extraction_agent",
        agent_title="Extract radiomics",
        instructions="Use accepted spleen masks only.",
        context_augmentation=ContextAugmentation(
            knowledge_summary="Spleen radiomics context",
            methodology_guidance="Use fixed bin width.",
        ),
        hypothesis_summary="Features predict outcome.",
        payload={"bin_width": 25},
    )

    restored = TaskMessage.model_validate_json(message.model_dump_json())

    assert restored == message
    assert restored.context_augmentation.methodology_guidance == (
        "Use fixed bin width."
    )
    assert restored.payload["bin_width"] == 25


@pytest.mark.contract
def test_task_message_mutable_defaults_are_isolated():
    first = TaskMessage()
    second = TaskMessage()

    first.payload["changed"] = True
    first.context_augmentation.knowledge_summary = "changed"

    assert second.payload == {}
    assert second.context_augmentation.knowledge_summary == ""

