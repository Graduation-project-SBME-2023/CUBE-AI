from __future__ import annotations

import pytest

from agents.planning_agents.context_compiler import (
    build_enriched_pipeline_prompt,
    compile_task_messages,
    format_task_prompt,
    summarize_knowledge_for_planning,
)
from models.plans import ClinicalHypothesis, StudyPlan, StudyStep


AGENT_ORDER = (
    "study_population_planner_agent",
    "segmentation_agent",
    "feature_extraction_agent",
    "modeling_agent",
    "result_reporter_agent",
    "latex_reporter_agent",
)


def _hypothesis() -> ClinicalHypothesis:
    return ClinicalHypothesis(
        title="Whole-spleen radiomics predicts malignant involvement",
        population="Adults with contrast-enhanced abdominal CT",
        intervention="Whole-spleen radiomics",
        comparison="Clinical variables",
        outcome="Malignant involvement",
    )


def _plan() -> StudyPlan:
    return StudyPlan(
        study_goal="Test a spleen radiomics classifier",
        steps=[
            StudyStep(
                id=f"step-{index}",
                title=agent_id.replace("_", " ").title(),
                description=f"Execute {agent_id} safely.",
                module=agent_id,
            )
            for index, agent_id in enumerate(AGENT_ORDER)
        ],
    )


@pytest.mark.unit
def test_compiler_creates_ordered_messages_for_every_execution_agent():
    plan = _plan()
    messages = compile_task_messages(
        _hypothesis(),
        plan,
        knowledge_sections={
            "anatomy_pathology": "Spleen anatomy",
            "literature_evidence": "Radiomics evidence",
            "method_trends": "Feature standardization",
        },
        sources_guide="PubMed and clinical guidelines",
        pipeline_instructions={
            agent_id: f"Specific instruction for {agent_id}"
            for agent_id in AGENT_ORDER
        },
    )

    assert [message.agent_id for message in messages] == list(AGENT_ORDER)
    assert all(message.phase != "unknown" for message in messages)
    assert all(message.task_id for message in messages)
    assert all(step.task_message is message for step, message in zip(plan.steps, messages))
    assert all("Study-Specific Instructions" in message.instructions for message in messages)


@pytest.mark.unit
def test_locked_roi_is_added_only_to_segmentation_instructions():
    messages = compile_task_messages(
        _hypothesis(),
        _plan(),
        knowledge_sections={},
        sources_guide="",
        locked_roi_interpretation={
            "scope": "whole_organ",
            "labels": ["spleen"],
        },
    )

    segmentation = next(
        message for message in messages if message.agent_id == "segmentation_agent"
    )
    assert "Locked ROI Interpretation" in segmentation.instructions
    assert '"spleen"' in segmentation.instructions
    assert all(
        "Locked ROI Interpretation" not in message.instructions
        for message in messages
        if message.agent_id != "segmentation_agent"
    )


@pytest.mark.unit
def test_formatter_and_pipeline_prompt_include_identity_and_overview():
    messages = compile_task_messages(
        _hypothesis(),
        _plan(),
        knowledge_sections={},
        sources_guide="",
    )

    first_prompt = format_task_prompt(messages[0])
    pipeline_prompt = build_enriched_pipeline_prompt(messages)

    assert messages[0].agent_title in first_prompt
    assert "HYPOTHESIS" in first_prompt
    assert "PIPELINE OVERVIEW" in pipeline_prompt
    assert all(message.agent_title in pipeline_prompt for message in messages)


@pytest.mark.unit
def test_knowledge_summary_respects_empty_and_bounded_inputs():
    hypothesis = _hypothesis()
    assert summarize_knowledge_for_planning({}, hypothesis) == (
        "No domain knowledge available."
    )

    summary = summarize_knowledge_for_planning(
        {
            "anatomy_pathology": "A" * 1000,
            "literature_evidence": "B" * 1000,
            "method_trends": "C" * 1000,
        },
        hypothesis,
        max_chars=1000,
    )
    assert "Clinical Context" in summary
    assert "Key Evidence" in summary
    assert "Method Trends" in summary
    assert len(summary) < 1500

