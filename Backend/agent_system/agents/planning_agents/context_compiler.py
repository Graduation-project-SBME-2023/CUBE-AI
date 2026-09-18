"""Context Compiler — AOT (Ahead-of-Time) Task Message Generator"""

import json
import uuid
import logging
from typing import Any, List, Dict, Optional, Tuple

from models.plans import (
    ClinicalHypothesis,
    StudyPlan,
    TaskMessage,
    ContextAugmentation,
)
from ..clinical_roi_policy import (
    ROI_IDENTITY_POLICY,
    canonicalize_voxtell_prompt_instructions,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# HYPOTHESIS SUMMARIZER
# ═══════════════════════════════════════════════════════════════════════════════

def _build_hypothesis_summary(hypothesis: ClinicalHypothesis) -> str:
    """Create a compact, structured PICO hypothesis summary for agent prompts."""
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
    """Truncate text to max_chars, preferring sentence boundaries."""
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
    """Build context for the population planner (cohort discovery) agent."""
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
        "- Label each selected volume in the cohort so downstream agents can read its target"
    )
    return ctx, instructions


def _build_segmentation_context(
    sections: Dict[str, str],
    sources_guide: str,
    hypothesis: ClinicalHypothesis,
) -> Tuple[ContextAugmentation, str]:
    """Build context for the segmentation agent."""
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

    # Include every PICO field because the explicit extraction ROI is commonly
    # stated in the intervention or research question rather than the title.
    hyp_block = _build_hypothesis_summary(hypothesis)

    instructions = (
        f"Perform ROI segmentation for the following study:\n"
        f"{hyp_block}\n\n"
        "Apply the authoritative ROI identity policy below. It overrides any\n"
        "contradictory target suggestion in generated study instructions:\n\n"
        f"{ROI_IDENTITY_POLICY}\n"
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
    """Build context for the radiomics feature extraction agent."""
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
    """Build context for the ML modeling agent."""
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
    """Build context for the result analysis agent."""
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
    """Build context for the LaTeX manuscript generation agent."""
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
    locked_roi_interpretation: Optional[Dict[str, Any]] = None,
) -> List[TaskMessage]:
    """Compile structured task messages for every step in the study plan."""
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

        if agent_id == "segmentation_agent":
            instruction_parts.append(
                "## Authoritative ROI Identity Policy\n"
                f"{ROI_IDENTITY_POLICY}"
            )
            if locked_roi_interpretation:
                instruction_parts.append(
                    "## Locked ROI Interpretation\n"
                    f"{json.dumps(locked_roi_interpretation, ensure_ascii=False, indent=2)}\n"
                    "Preserve exactly this ROI scope and these labels."
                )

        # Layer 2: Pipeline Architect's study-specific instructions
        planner_instr = pipeline_instructions.get(agent_id, "")
        if agent_id == "segmentation_agent" and planner_instr:
            canonical_labels = (
                locked_roi_interpretation.get("labels", [])
                if locked_roi_interpretation
                else []
            )
            planner_instr = canonicalize_voxtell_prompt_instructions(
                planner_instr,
                canonical_labels,
            )
        if planner_instr:
            instruction_parts.append(
                f"## Study-Specific Instructions\n{planner_instr}"
            )

        # Layer 3: Planner's step description
        if step.description:
            instruction_parts.append(
                f"## Step Description\n{step.description}"
            )

        # Layer 4: Domain-informed guidance from knowledge base
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
    """Format a single TaskMessage into a text prompt for AutoGen agents."""
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
    """Create a balanced, structured summary of the knowledge base for the Pipeline Architect prompt."""
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
    """Build a complete pipeline task prompt from all task messages."""
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
