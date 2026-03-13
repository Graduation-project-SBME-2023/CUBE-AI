"""
Context Compiler — AOT (Ahead-of-Time) task message generator.

After the planner confirms a hypothesis and the knowledge builder fetches
domain knowledge, the Context Compiler:
  1. Receives pre-structured knowledge sections from the knowledge builder.
  2. Selects the relevant knowledge slice for each execution agent.
  3. Assembles structured TaskMessage objects with:
        - agent-specific instructions (contextualized to the hypothesis)
        - context_augmentation (knowledge, literature, terminology, methods)
        - hypothesis summary
  4. Returns the list of TaskMessages — one per pipeline step.

Pipeline: knowledge_builder → (structured sections) → context_compiler → task messages

This implements the "AOT Context Compiler" from arch.html.
"""

import uuid
import logging
from typing import List, Dict, Optional, Tuple

from .models import (
    ClinicalHypothesis,
    StudyPlan,
    StudyStep,
    TaskMessage,
    ContextAugmentation,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# HYPOTHESIS SUMMARIZER
# ══════════════════════════════════════════════════════════════════════════════

def _build_hypothesis_summary(hypothesis: ClinicalHypothesis) -> str:
    """Create a compact, structured PICO hypothesis summary for agent prompts."""
    def _field_str(val):
        if isinstance(val, dict):
            return ", ".join(f"{k}: {v}" for k, v in val.items())
        return str(val) if val else ""

    parts = [f"Study: {hypothesis.title}"]

    pop = _field_str(hypothesis.population)
    if pop:
        parts.append(f"Population: {pop}")

    interv = _field_str(hypothesis.intervention)
    if interv:
        parts.append(f"Intervention: {interv}")

    comp = _field_str(hypothesis.comparison)
    if comp:
        parts.append(f"Comparison: {comp}")

    outcome = _field_str(hypothesis.outcome)
    if outcome:
        parts.append(f"Outcome: {outcome}")

    if hypothesis.hypothesis:
        parts.append(f"Hypothesis: {hypothesis.hypothesis}")

    if hypothesis.question:
        parts.append(f"Research Question: {hypothesis.question}")

    if hypothesis.clinical_context:
        parts.append(f"Clinical Context: {hypothesis.clinical_context}")

    if hypothesis.relationship_type:
        parts.append(f"Relationship: {hypothesis.relationship_type}")

    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# AGENT-PHASE MAPPING
# ══════════════════════════════════════════════════════════════════════════════

_PHASE_MAP = {
    "study_population_planner_agent": "cohort_discovery",
    "segmentation_agent":             "segmentation",
    "feature_extraction_agent":       "feature_extraction",
    "modeling_agent":                  "modeling",
    "result_reporter_agent":          "result_analysis",
    "latex_reporter_agent":           "manuscript_generation",
}


def _truncate(text: str, max_chars: int = 4000) -> str:
    """Truncate text to max_chars, ensuring we don't cut mid-sentence."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    last_period = cut.rfind(".")
    if last_period > max_chars * 0.7:
        return cut[:last_period + 1] + "\n[...truncated]"
    return cut + "\n[...truncated]"


# ══════════════════════════════════════════════════════════════════════════════
# PER-AGENT CONTEXT BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

def _build_population_context(
    sections: Dict[str, str],
    sources_guide: str,
    hypothesis: ClinicalHypothesis,
) -> Tuple[ContextAugmentation, str]:
    """Context for the population planner agent."""
    ctx = ContextAugmentation(
        knowledge_summary=_truncate(
            sections.get("anatomy_pathology", ""),
            3000,
        ),
        literature_findings=_truncate(
            sections.get("clinical_guidelines", ""),
            2000,
        ),
        methodology_guidance=(
            "Use the clinical context and inclusion/exclusion criteria from the literature "
            "to select an appropriate study cohort. Ensure demographic balance and adequate sample size."
        ),
        terminology=_truncate(
            sections.get("mesh_terminology", ""),
            1500,
        ),
        sources_guide=_truncate(sources_guide, 1500),
    )
    instructions = (
        f"Select the study population for the hypothesis: '{hypothesis.title}'.\n"
        f"Population definition: {hypothesis.population}\n\n"
        "Use the domain knowledge in your context to:\n"
        "- Define precise inclusion/exclusion criteria based on clinical guidelines\n"
        "- Apply correct medical terminology (MeSH terms provided)\n"
        "- Ensure the cohort matches the literature-recommended study design\n"
        "- Generate a targets.csv mapping each selected volume to its target label"
    )
    return ctx, instructions


def _build_segmentation_context(
    sections: Dict[str, str],
    sources_guide: str,
    hypothesis: ClinicalHypothesis,
) -> Tuple[ContextAugmentation, str]:
    """Context for the segmentation agent."""
    ctx = ContextAugmentation(
        knowledge_summary=_truncate(
            sections.get("anatomy_pathology", ""),
            2000,
        ),
        literature_findings=_truncate(
            sections.get("literature_evidence", ""),
            1500,
        ),
        methodology_guidance=_truncate(
            sections.get("method_trends", ""),
            1500,
        ),
        terminology=_truncate(
            sections.get("mesh_terminology", ""),
            1000,
        ),
        sources_guide="",
    )
    instructions = (
        f"Perform ROI segmentation for the hypothesis: '{hypothesis.title}'.\n\n"
        "Use the domain knowledge in your context to:\n"
        "- Identify the correct anatomical regions to segment based on clinical literature\n"
        "- Follow evidence-based segmentation protocols mentioned in recent studies\n"
        "- Apply appropriate segmentation methods for the imaging modality described\n"
        "- Ensure segmented ROIs match the standard terminology from MeSH"
    )
    return ctx, instructions


def _build_feature_extraction_context(
    sections: Dict[str, str],
    sources_guide: str,
    hypothesis: ClinicalHypothesis,
) -> Tuple[ContextAugmentation, str]:
    """Context for the feature extraction agent."""
    ctx = ContextAugmentation(
        knowledge_summary=_truncate(
            sections.get("anatomy_pathology", ""),
            1500,
        ),
        literature_findings=_truncate(
            sections.get("literature_evidence", ""),
            2500,
        ),
        methodology_guidance=_truncate(
            sections.get("method_trends", ""),
            2500,
        ),
        terminology=_truncate(
            sections.get("mesh_terminology", ""),
            1500,
        ),
        sources_guide="",
    )
    instructions = (
        f"Extract radiomics features for the hypothesis: '{hypothesis.title}'.\n\n"
        "Use the domain knowledge in your context to:\n"
        "- Select feature classes (shape, firstorder, GLCM, GLRLM, etc.) that the literature "
        "identifies as most discriminative for this pathology\n"
        "- Apply normalization and preprocessing consistent with published radiomics studies\n"
        "- Focus feature importance analysis on features known to correlate with the outcome\n"
        "- Reference the MeSH terminology for correct feature naming"
    )
    return ctx, instructions


def _build_modeling_context(
    sections: Dict[str, str],
    sources_guide: str,
    hypothesis: ClinicalHypothesis,
) -> Tuple[ContextAugmentation, str]:
    """Context for the modeling agent."""
    ctx = ContextAugmentation(
        knowledge_summary="",
        literature_findings=_truncate(
            sections.get("literature_evidence", ""),
            3000,
        ),
        methodology_guidance=_truncate(
            sections.get("method_trends", ""),
            3000,
        ),
        terminology="",
        sources_guide="",
    )
    instructions = (
        f"Train and evaluate classification models for: '{hypothesis.title}'.\n\n"
        "Use the domain knowledge in your context to:\n"
        "- Select model architectures that recent literature has validated for this type of radiomics study\n"
        "- Apply evaluation metrics (AUC, sensitivity, specificity) consistent with published benchmarks\n"
        "- Compare your results against the state-of-the-art performance reported in the literature\n"
        "- Use the method trends to justify model selection and hyperparameter choices"
    )
    return ctx, instructions


def _build_result_reporter_context(
    sections: Dict[str, str],
    sources_guide: str,
    hypothesis: ClinicalHypothesis,
) -> Tuple[ContextAugmentation, str]:
    """Context for the result reporter agent."""
    ctx = ContextAugmentation(
        knowledge_summary=_truncate(
            sections.get("anatomy_pathology", ""),
            1500,
        ),
        literature_findings=_truncate(
            sections.get("literature_evidence", ""),
            3000,
        ),
        methodology_guidance=_truncate(
            sections.get("clinical_guidelines", ""),
            2000,
        ),
        terminology=_truncate(
            sections.get("mesh_terminology", ""),
            1000,
        ),
        sources_guide=_truncate(sources_guide, 1500),
    )
    instructions = (
        f"Analyze results and validate the hypothesis: '{hypothesis.title}'.\n\n"
        "Use the domain knowledge in your context to:\n"
        "- Compare model performance against literature-reported benchmarks\n"
        "- Evaluate whether findings support or reject the hypothesis using clinical evidence\n"
        "- Reference published guidelines for clinical significance thresholds\n"
        "- Provide a scientifically rigorous justification citing relevant literature trends\n"
        "- Structure the report following published radiomics study reporting standards"
    )
    return ctx, instructions


def _build_latex_reporter_context(
    sections: Dict[str, str],
    sources_guide: str,
    hypothesis: ClinicalHypothesis,
) -> Tuple[ContextAugmentation, str]:
    """Context for the LaTeX reporter agent."""
    ctx = ContextAugmentation(
        knowledge_summary="",
        literature_findings=_truncate(
            sections.get("literature_evidence", ""),
            2000,
        ),
        methodology_guidance="",
        terminology="",
        sources_guide=_truncate(sources_guide, 2000),
    )
    journal_info = _truncate(sections.get("journal_landscape", ""), 1500)
    instructions = (
        f"Generate a LaTeX manuscript for the study: '{hypothesis.title}'.\n\n"
        "Use the domain knowledge in your context to:\n"
        "- Format the paper using conventions of journals identified in the sources guide\n"
        "- Include proper citations and references based on the literature findings\n"
        "- Structure the manuscript with Introduction, Methods, Results, Discussion sections\n"
        "- Ensure clinical terminology matches established MeSH vocabulary\n"
    )
    if journal_info:
        instructions += f"\nTarget journals and formatting context:\n{journal_info}"

    return ctx, instructions


# Agent ID → context builder mapping
_CONTEXT_BUILDERS = {
    "study_population_planner_agent": _build_population_context,
    "segmentation_agent":             _build_segmentation_context,
    "feature_extraction_agent":       _build_feature_extraction_context,
    "modeling_agent":                  _build_modeling_context,
    "result_reporter_agent":          _build_result_reporter_context,
    "latex_reporter_agent":           _build_latex_reporter_context,
}


# ══════════════════════════════════════════════════════════════════════════════
# MAIN COMPILER FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def compile_task_messages(
    hypothesis: ClinicalHypothesis,
    plan: StudyPlan,
    knowledge_sections: Dict[str, str],
    sources_guide: str,
    pipeline_instructions: Optional[Dict[str, str]] = None,
) -> List[TaskMessage]:
    """
    Compile structured task messages for every step in the study plan.

    Each TaskMessage includes:
        - Unique task_id
        - Pipeline phase name
        - Agent-specific instructions enriched with hypothesis context
        - Pipeline architect's detailed instructions (from dynamic planning)
        - context_augmentation with relevant knowledge slices
        - Hypothesis summary

    Args:
        hypothesis:            The confirmed clinical hypothesis.
        plan:                  The confirmed study plan with customized steps.
        knowledge_sections:    Pre-structured dict from knowledge_builder mapping
                               canonical section keys (anatomy_pathology,
                               mesh_terminology, literature_evidence, etc.)
                               to their text content.
        sources_guide:         Sources guide text from knowledge_builder.
        pipeline_instructions: Dict mapping agent_id → detailed instructions
                               from the Pipeline Architect (Phase 3).

    Returns:
        List of TaskMessage objects, one per plan step, ordered by pipeline phase.
    """
    logger.info("Context Compiler: Assembling task messages for %d steps", len(plan.steps))
    pipeline_instructions = pipeline_instructions or {}
    sections = knowledge_sections or {}

    logger.info("Using %d pre-structured knowledge sections: %s", len(sections), list(sections.keys()))

    # Build compact hypothesis summary (shared across all agents)
    hyp_summary = _build_hypothesis_summary(hypothesis)

    task_messages: List[TaskMessage] = []

    for step in plan.steps:
        agent_id = step.module or ""
        phase = _PHASE_MAP.get(agent_id, "unknown")

        # Get agent-specific context builder for domain knowledge slicing
        builder_fn = _CONTEXT_BUILDERS.get(agent_id)
        if builder_fn:
            ctx, domain_guidance = builder_fn(sections, sources_guide, hypothesis)
        else:
            ctx = ContextAugmentation()
            domain_guidance = step.description

        # Build layered instructions:
        #   1. Pipeline Architect's study-specific instructions (top priority)
        #   2. Planner's step description
        #   3. Domain-informed guidance from knowledge base
        instruction_parts = []

        planner_instr = pipeline_instructions.get(agent_id, "")
        if planner_instr:
            instruction_parts.append(f"## Study-Specific Instructions\n{planner_instr}")

        if step.description:
            instruction_parts.append(f"## Step Description\n{step.description}")

        if domain_guidance:
            instruction_parts.append(f"## Domain-Informed Guidance\n{domain_guidance}")

        instructions = "\n\n".join(instruction_parts) if instruction_parts else step.description

        task_msg = TaskMessage(
            task_id=str(uuid.uuid4()),
            phase=phase,
            agent_id=agent_id,
            agent_title=step.name,
            instructions=instructions,
            context_augmentation=ctx,
            hypothesis_summary=hyp_summary,
        )
        task_messages.append(task_msg)

        logger.info(
            "  [%s] %s — context: kb=%d lit=%d meth=%d term=%d instr=%d chars",
            phase,
            agent_id,
            len(ctx.knowledge_summary),
            len(ctx.literature_findings),
            len(ctx.methodology_guidance),
            len(ctx.terminology),
            len(instructions),
        )

    logger.info("Context Compiler: Generated %d task messages", len(task_messages))
    return task_messages


def format_task_prompt(task_message: TaskMessage) -> str:
    """
    Format a single TaskMessage into a text prompt suitable for passing
    to an AutoGen agent via TextMessage.

    This is the bridge between the structured TaskMessage and the
    plain-text interface of the execution agents.
    """
    parts = []

    # Header
    parts.append(f"═══ TASK: {task_message.agent_title} (Phase: {task_message.phase}) ═══\n")

    # Hypothesis context
    if task_message.hypothesis_summary:
        parts.append(f"── HYPOTHESIS ──\n{task_message.hypothesis_summary}\n")

    # Instructions
    if task_message.instructions:
        parts.append(f"── INSTRUCTIONS ──\n{task_message.instructions}\n")

    # Context augmentation sections
    ctx = task_message.context_augmentation
    if ctx.knowledge_summary:
        parts.append(f"── DOMAIN KNOWLEDGE ──\n{ctx.knowledge_summary}\n")
    if ctx.literature_findings:
        parts.append(f"── LITERATURE FINDINGS ──\n{ctx.literature_findings}\n")
    if ctx.methodology_guidance:
        parts.append(f"── METHODOLOGY GUIDANCE ──\n{ctx.methodology_guidance}\n")
    if ctx.terminology:
        parts.append(f"── TERMINOLOGY ──\n{ctx.terminology}\n")
    if ctx.sources_guide:
        parts.append(f"── SOURCES & REFERENCES ──\n{ctx.sources_guide}\n")

    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE SUMMARIZER FOR PIPELINE PLANNING
# ══════════════════════════════════════════════════════════════════════════════

def summarize_knowledge_for_planning(
    knowledge_sections: Dict[str, str],
    hypothesis: ClinicalHypothesis,
    max_chars: int = 6000,
) -> str:
    """
    Create a balanced, structured summary of the knowledge base for the
    Pipeline Architect prompt.

    Instead of blindly truncating the full KB text (which biases towards
    whichever section appears first), this draws from every section
    proportionally so the planner sees anatomy, evidence, methods,
    guidelines, and datasets — all in one concise block.

    Args:
        knowledge_sections: Pre-structured dict from knowledge_builder.
        hypothesis:         The confirmed clinical hypothesis.
        max_chars:          Total character budget for the summary.

    Returns:
        Summarized knowledge string for use in the pipeline planning prompt.
    """
    if not knowledge_sections:
        return "No domain knowledge available."

    hyp_summary = _build_hypothesis_summary(hypothesis)

    # Allocate character budget across sections proportionally
    _SECTION_CONFIG = [
        ("anatomy_pathology",  "Clinical Context",   0.15),
        ("literature_evidence", "Key Evidence",       0.25),
        ("method_trends",      "Method Trends",       0.20),
        ("clinical_guidelines", "Clinical Guidelines", 0.15),
        ("mesh_terminology",   "Key Terminology",      0.10),
        ("dataset_refs",       "Reference Datasets",   0.08),
        ("journal_landscape",  "Target Journals",      0.07),
    ]

    parts = [f"── HYPOTHESIS ──\n{hyp_summary}\n"]
    remaining = max_chars - len(parts[0])

    for key, label, weight in _SECTION_CONFIG:
        text = knowledge_sections.get(key, "")
        if not text:
            continue
        budget = int(remaining * weight)
        snippet = _truncate(text, budget)
        parts.append(f"── {label} ──\n{snippet}")

    return "\n\n".join(parts)


def build_enriched_pipeline_prompt(task_messages: List[TaskMessage]) -> str:
    """
    Build a complete pipeline task prompt from all task messages.

    This is used as the `task=` parameter for GraphFlow.run_stream().
    The first agent (population planner) gets the full prompt; subsequent
    agents receive their own task messages via MessageFilterAgent.
    """
    if not task_messages:
        return "Run the pipeline."

    # The first task message is for the population planner — it gets
    # the most comprehensive prompt since it kicks off the pipeline.
    first_msg = task_messages[0]
    prompt = format_task_prompt(first_msg)

    # Add a compact summary of the full pipeline for context
    prompt += "\n── PIPELINE OVERVIEW ──\n"
    for i, tm in enumerate(task_messages):
        prompt += f"  {i+1}. [{tm.phase}] {tm.agent_title}: {tm.instructions[:100]}...\n"

    return prompt
