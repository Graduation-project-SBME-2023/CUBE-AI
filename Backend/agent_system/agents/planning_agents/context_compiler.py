"""
Context Compiler — AOT (Ahead-of-Time) Task Message Generator

After the planner confirms a hypothesis and the knowledge builder fetches
domain knowledge, the Context Compiler:
  1. Receives pre-structured knowledge sections from the knowledge builder.
  2. Selects the relevant knowledge slice for each execution agent.
  3. Assembles structured TaskMessage objects with:
        - Agent-specific instructions (contextualized to the hypothesis)
        - Context augmentation (knowledge, literature, terminology, methods)
        - Hypothesis summary
  4. Returns the list of TaskMessages — one per pipeline step.

Pipeline:
    knowledge_builder → (structured sections) → context_compiler → task messages

This implements the "AOT Context Compiler" from arch.html.
"""

import uuid
import logging
from typing import List, Dict, Optional, Tuple

from models.plans import (
    ClinicalHypothesis,
    StudyPlan,
    TaskMessage,
    ContextAugmentation,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# HYPOTHESIS SUMMARIZER
# ═══════════════════════════════════════════════════════════════════════════════

def _build_hypothesis_summary(hypothesis: ClinicalHypothesis) -> str:
    """
    Create a compact, structured PICO hypothesis summary for agent prompts.

    Formats all PICO components (Population, Intervention, Comparison, Outcome)
    plus the research question and clinical context into a readable text block
    that can be prepended to any agent's task instructions.

    Args:
        hypothesis: The confirmed clinical hypothesis with full PICO fields.

    Returns:
        Formatted multi-line string summarizing the hypothesis.
    """
    def _field_str(val):
        """Handle nested dict fields gracefully."""
        if isinstance(val, dict):
            return ", ".join(f"{k}: {v}" for k, v in val.items())
        return str(val) if val else ""

    parts = [f"Study: {hypothesis.title}"]

    # Build each PICO component if present
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


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT-TO-PHASE MAPPING
# ═══════════════════════════════════════════════════════════════════════════════

# Maps each agent ID to its canonical pipeline phase name
_PHASE_MAP = {
    "study_population_planner_agent": "cohort_discovery",
    "segmentation_agent":             "segmentation",
    "feature_extraction_agent":       "feature_extraction",
    "modeling_agent":                 "modeling",
    "result_reporter_agent":          "result_analysis",
    "latex_reporter_agent":           "manuscript_generation",
}


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY: TRUNCATION
# ═══════════════════════════════════════════════════════════════════════════════

def _truncate(text: str, max_chars: int = 4000) -> str:
    """
    Truncate text to max_chars, preferring sentence boundaries.

    Avoids cutting mid-sentence by backtracking to the last period
    if it's within a reasonable range (70% of max_chars).

    Args:
        text: The text to truncate.
        max_chars: Maximum allowed characters.

    Returns:
        Truncated text with "[...truncated]" marker if shortened.
    """
    if len(text) <= max_chars:
        return text

    cut = text[:max_chars]
    last_period = cut.rfind(".")

    # Only use sentence boundary if it's not too far back
    if last_period > max_chars * 0.7:
        return cut[:last_period + 1] + "\n[...truncated]"

    return cut + "\n[...truncated]"


# ═══════════════════════════════════════════════════════════════════════════════
# PER-AGENT CONTEXT BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════
# Each builder function receives the knowledge sections, sources guide, and
# hypothesis, and returns (ContextAugmentation, domain_guidance_instructions).
# The knowledge slices are tailored to what each agent actually needs.

def _build_population_context(
    sections: Dict[str, str],
    sources_guide: str,
    hypothesis: ClinicalHypothesis,
) -> Tuple[ContextAugmentation, str]:
    """
    Build context for the population planner (cohort discovery) agent.

    Prioritizes anatomy/pathology knowledge and clinical guidelines
    to inform inclusion/exclusion criteria and cohort selection.
    """
    ctx = ContextAugmentation(
        knowledge_summary=_truncate(
            sections.get("anatomy_pathology", ""), 3000,
        ),
        literature_findings=_truncate(
            sections.get("clinical_guidelines", ""), 2000,
        ),
        methodology_guidance=(
            "Use the clinical context and inclusion/exclusion criteria from "
            "the literature to select an appropriate study cohort. Ensure "
            "demographic balance and adequate sample size."
        ),
        terminology=_truncate(
            sections.get("mesh_terminology", ""), 1500,
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
    """
    Build context for the segmentation agent.

    Focuses on anatomical knowledge, method trends, and terminology
    to guide ROI selection and segmentation protocols.
    """
    ctx = ContextAugmentation(
        knowledge_summary=_truncate(
            sections.get("anatomy_pathology", ""), 2000,
        ),
        literature_findings=_truncate(
            sections.get("literature_evidence", ""), 1500,
        ),
        methodology_guidance=_truncate(
            sections.get("method_trends", ""), 1500,
        ),
        terminology=_truncate(
            sections.get("mesh_terminology", ""), 1000,
        ),
        sources_guide="",  # Not needed for segmentation
    )

    # Build a rich hypothesis context block so the PlannerAgent can
    # distinguish between healthy-organ vs. pathology-on-organ requests.
    hyp_context_parts = [f"Study title: {hypothesis.title}"]
    if hypothesis.hypothesis:
        hyp_context_parts.append(f"Hypothesis: {hypothesis.hypothesis}")
    if hypothesis.population:
        pop = hypothesis.population
        if isinstance(pop, dict):
            pop = ", ".join(f"{k}: {v}" for k, v in pop.items())
        hyp_context_parts.append(f"Population: {pop}")
    if hypothesis.clinical_context:
        hyp_context_parts.append(f"Clinical context: {hypothesis.clinical_context}")
    hyp_block = "\n".join(hyp_context_parts)

    instructions = (
        f"Perform ROI segmentation for the following study:\n"
        f"{hyp_block}\n\n"
        "CRITICAL — Read the hypothesis carefully to determine the segmentation target:\n"
        "- If the hypothesis studies a DISEASE or PATHOLOGY of an organ (e.g., 'cancerous spleens',\n"
        "  'liver tumors', 'lung nodules'), the segmentation target is the PATHOLOGY on that organ\n"
        "  (e.g., 'spleen lesion', 'liver tumor', 'lung nodule'), NOT the healthy organ alone.\n"
        "- If the hypothesis studies the organ itself with no disease mentioned (e.g., 'spleen volume',\n"
        "  'liver morphology'), the segmentation target is the healthy organ.\n\n"
        "Use the domain knowledge in your context to:\n"
        "- Identify the correct anatomical regions to segment based on the hypothesis and clinical literature\n"
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
    """
    Build context for the radiomics feature extraction agent.

    Emphasizes literature evidence and method trends to guide feature
    selection, normalization, and preprocessing choices.
    """
    ctx = ContextAugmentation(
        knowledge_summary=_truncate(
            sections.get("anatomy_pathology", ""), 1500,
        ),
        literature_findings=_truncate(
            sections.get("literature_evidence", ""), 2500,
        ),
        methodology_guidance=_truncate(
            sections.get("method_trends", ""), 2500,
        ),
        terminology=_truncate(
            sections.get("mesh_terminology", ""), 1500,
        ),
        sources_guide="",
    )

    instructions = (
        f"Extract radiomics features for the hypothesis: '{hypothesis.title}'.\n\n"
        "Use the domain knowledge in your context to:\n"
        "- Select feature classes (shape, firstorder, GLCM, GLRLM, etc.) that the "
        "literature identifies as most discriminative for this pathology\n"
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
    """
    Build context for the ML modeling agent.

    Prioritizes literature evidence and method trends to inform model
    selection, evaluation metrics, and benchmarking against published results.
    """
    ctx = ContextAugmentation(
        knowledge_summary="",  # Modeling doesn't need anatomical knowledge
        literature_findings=_truncate(
            sections.get("literature_evidence", ""), 3000,
        ),
        methodology_guidance=_truncate(
            sections.get("method_trends", ""), 3000,
        ),
        terminology="",
        sources_guide="",
    )

    instructions = (
        f"Train and evaluate classification models for: '{hypothesis.title}'.\n\n"
        "Use the domain knowledge in your context to:\n"
        "- Select model architectures that recent literature has validated for this "
        "type of radiomics study\n"
        "- Apply evaluation metrics (AUC, sensitivity, specificity) consistent with "
        "published benchmarks\n"
        "- Compare your results against the state-of-the-art performance reported in "
        "the literature\n"
        "- Use the method trends to justify model selection and hyperparameter choices"
    )
    return ctx, instructions


def _build_result_reporter_context(
    sections: Dict[str, str],
    sources_guide: str,
    hypothesis: ClinicalHypothesis,
) -> Tuple[ContextAugmentation, str]:
    """
    Build context for the result analysis agent.

    Provides comprehensive literature evidence, clinical guidelines, and
    sources for benchmarking, hypothesis validation, and scientific interpretation.
    """
    ctx = ContextAugmentation(
        knowledge_summary=_truncate(
            sections.get("anatomy_pathology", ""), 1500,
        ),
        literature_findings=_truncate(
            sections.get("literature_evidence", ""), 3000,
        ),
        methodology_guidance=_truncate(
            sections.get("clinical_guidelines", ""), 2000,
        ),
        terminology=_truncate(
            sections.get("mesh_terminology", ""), 1000,
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
    """
    Build context for the LaTeX manuscript generation agent.

    Focuses on literature evidence, sources for citations, and journal
    landscape information to guide formatting and reference management.
    """
    ctx = ContextAugmentation(
        knowledge_summary="",
        literature_findings=_truncate(
            sections.get("literature_evidence", ""), 2000,
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

    # Append journal-specific formatting guidance if available
    if journal_info:
        instructions += f"\nTarget journals and formatting context:\n{journal_info}"

    return ctx, instructions


# Registry mapping agent IDs to their context builder functions
_CONTEXT_BUILDERS = {
    "study_population_planner_agent": _build_population_context,
    "segmentation_agent":             _build_segmentation_context,
    "feature_extraction_agent":       _build_feature_extraction_context,
    "modeling_agent":                 _build_modeling_context,
    "result_reporter_agent":          _build_result_reporter_context,
    "latex_reporter_agent":           _build_latex_reporter_context,
}


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN COMPILER FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

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
        - Unique task_id (UUID)
        - Pipeline phase name
        - Agent-specific instructions enriched with hypothesis context
        - Pipeline architect's detailed instructions (from dynamic planning)
        - Context augmentation with relevant knowledge slices
        - Hypothesis summary for shared context

    The instructions are layered in priority order:
        1. Pipeline Architect's study-specific instructions (highest priority)
        2. Planner's step description
        3. Domain-informed guidance from knowledge base

    Args:
        hypothesis:            The confirmed clinical hypothesis with PICO fields.
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
    logger.info(
        "Context Compiler: Assembling task messages for %d steps",
        len(plan.steps),
    )
    pipeline_instructions = pipeline_instructions or {}
    sections = knowledge_sections or {}

    logger.info(
        "Using %d pre-structured knowledge sections: %s",
        len(sections), list(sections.keys()),
    )

    # Build compact hypothesis summary shared across all agents
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
            # Fallback for unknown agents — empty context, use description as guidance
            ctx = ContextAugmentation()
            domain_guidance = step.description

        # Build layered instructions in priority order
        instruction_parts = []

        # Layer 1: Pipeline Architect's study-specific instructions (highest priority)
        planner_instr = pipeline_instructions.get(agent_id, "")
        if planner_instr:
            instruction_parts.append(
                f"## Study-Specific Instructions\n{planner_instr}"
            )

        # Layer 2: Planner's step description
        if step.description:
            instruction_parts.append(
                f"## Step Description\n{step.description}"
            )

        # Layer 3: Domain-informed guidance from knowledge base
        if domain_guidance:
            instruction_parts.append(
                f"## Domain-Informed Guidance\n{domain_guidance}"
            )

        # Combine layers or fall back to description alone
        instructions = (
            "\n\n".join(instruction_parts)
            if instruction_parts
            else step.description
        )

        # Assemble the complete task message
        task_msg = TaskMessage(
            task_id=str(uuid.uuid4()),
            phase=phase,
            agent_id=agent_id,
            agent_title=step.title,
            instructions=instructions,
            context_augmentation=ctx,
            hypothesis_summary=hyp_summary,
        )
        task_messages.append(task_msg)

        # Attach task message back to the step for downstream access
        step.task_message = task_msg

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

    logger.info(
        "Context Compiler: Generated %d task messages",
        len(task_messages),
    )
    return task_messages


# ═══════════════════════════════════════════════════════════════════════════════
# TASK MESSAGE FORMATTER
# ═══════════════════════════════════════════════════════════════════════════════

def format_task_prompt(task_message: TaskMessage) -> str:
    """
    Format a single TaskMessage into a text prompt for AutoGen agents.

    This is the bridge between the structured TaskMessage model and the
    plain-text interface expected by execution agents. The output is
    suitable for passing via TextMessage to any agent in the pipeline.

    Args:
        task_message: A fully populated TaskMessage with instructions,
                      context augmentation, and hypothesis summary.

    Returns:
        Formatted multi-line string prompt.
    """
    parts = []

    # Header with task identity
    parts.append(
        f"═══ TASK: {task_message.agent_title} "
        f"(Phase: {task_message.phase}) ═══\n"
    )

    # Hypothesis context (shared across all agents)
    if task_message.hypothesis_summary:
        parts.append(f"── HYPOTHESIS ──\n{task_message.hypothesis_summary}\n")

    # Instructions (layered: pipeline architect + domain guidance)
    if task_message.instructions:
        parts.append(f"── INSTRUCTIONS ──\n{task_message.instructions}\n")

    # Context augmentation sections (in priority order)
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


# ═══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE SUMMARIZER FOR PIPELINE PLANNING
# ═══════════════════════════════════════════════════════════════════════════════

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

    Section allocation:
        - Clinical Context (anatomy/pathology):    15%
        - Key Evidence (literature):               25%
        - Method Trends:                           20%
        - Clinical Guidelines:                     15%
        - Key Terminology (MeSH):                  10%
        - Reference Datasets:                       8%
        - Target Journals:                          7%

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

    # Section configuration: (key, label, weight_percentage)
    _SECTION_CONFIG = [
        ("anatomy_pathology",   "Clinical Context",     0.15),
        ("literature_evidence", "Key Evidence",         0.25),
        ("method_trends",       "Method Trends",        0.20),
        ("clinical_guidelines", "Clinical Guidelines",  0.15),
        ("mesh_terminology",    "Key Terminology",      0.10),
        ("dataset_refs",        "Reference Datasets",   0.08),
        ("journal_landscape",   "Target Journals",      0.07),
    ]

    parts = [f"── HYPOTHESIS ──\n{hyp_summary}\n"]
    remaining = max_chars - len(parts[0])

    # Allocate budget proportionally across available sections
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

    Args:
        task_messages: Ordered list of task messages, one per pipeline step.

    Returns:
        Complete formatted prompt string for the pipeline entry point.
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
        preview = tm.instructions[:100] + "..." if len(tm.instructions) > 100 else tm.instructions
        prompt += f"  {i+1}. [{tm.phase}] {tm.agent_title}: {preview}\n"

    return prompt