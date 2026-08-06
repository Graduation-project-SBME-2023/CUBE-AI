import json
import logging
import re
from typing import Callable, TypeVar

from autogen_agentchat.agents import AssistantAgent
from pydantic import ValidationError

from .clinical_target_policy import (
    CLINICAL_TARGET_POLICY,
    CLINICAL_TARGET_POLICY_FINGERPRINT,
)
from .contracts import (
    CachePromptCandidate,
    ClinicalTargetIntent,
    ClinicalTargetIntentPlan,
    MaskLabelCandidate,
    REUSE_CONFIDENCE_THRESHOLD,
    RESOLVED_REASON_MAX_LENGTH,
    ResolvedTarget,
    TargetCandidateDecisionPlan,
    TargetResolutionOutcome,
    TargetResolutionPlan,
    validate_candidate_decision_references,
    validate_candidate_references,
)
from .model_catalog import get_enabled_segmentation_model_paths

logger = logging.getLogger(__name__)
ValidatedPlan = TypeVar("ValidatedPlan")

# ---------------------------------------------------------------------------
# Agent 1: Clinical ROI Interpreter
# ---------------------------------------------------------------------------

INTENT_SYSTEM_MESSAGE = f"""
You are ClinicalTargetIntentAgent. Your sole responsibility is to determine
the physical region that must be delineated — what voxels to include in the
mask — based on the clinical instruction. You must lock this identity before
any existing mask labels, cache prompts, or model names are considered.

Apply this clinician-reviewable policy:

{CLINICAL_TARGET_POLICY}

## Modifier test

For every qualifying word or phrase, ask:
  "Would removing this word change the voxel boundary, select a different
   lesion, or collapse a separately delineated subregion into a broader ROI?"

- YES → target-defining: keep in canonical_label, list in
  target_defining_modifiers.
- NO → contextual: put in contextual_descriptors only.

No modifier is universally identity-defining or universally contextual.
Judge from the full clinical instruction and locked scope.

## Four worked examples

### Example A — whole organ, no modifiers
Instruction: "Radiomic features extracted from the spleen ROI."
{{
  "target_id": "spleen",
  "canonical_label": "spleen",
  "anatomy": "spleen",
  "pathology": null,
  "scope": "whole_organ",
  "subregion": null,
  "laterality": "unspecified",
  "target_defining_modifiers": [],
  "contextual_descriptors": [],
  "evidence_span": "spleen ROI",
  "intent_confidence": 0.99,
  "rationale": "The instruction explicitly requests the complete spleen organ."
}}

### Example B — lesion subregion with target-defining modifier
Instruction: "Radiomic features extracted from the enhancing tumor ROI in
treated glioblastoma suspected progression."
{{
  "target_id": "enhancing_brain_tumor",
  "canonical_label": "enhancing brain tumor",
  "anatomy": "brain",
  "pathology": "tumor",
  "scope": "lesion_subregion",
  "subregion": "enhancing component",
  "laterality": "unspecified",
  "target_defining_modifiers": ["enhancing"],
  "contextual_descriptors": [
    "treated glioblastoma",
    "suspected progression"
  ],
  "evidence_span": "enhancing tumor ROI",
  "intent_confidence": 0.99,
  "rationale": "The requested ROI is the enhancing compartment of the tumor. Removing 'enhancing' would widen the boundary to include non-enhancing and necrotic regions, so it is target-defining."
}}

### Example C — acquisition modifier, not target-defining
Instruction: "Segment the brain tumor on contrast-enhanced MRI."
{{
  "target_id": "brain_tumor",
  "canonical_label": "brain tumor",
  "anatomy": "brain",
  "pathology": "tumor",
  "scope": "whole_lesion",
  "subregion": null,
  "laterality": "unspecified",
  "target_defining_modifiers": [],
  "contextual_descriptors": ["contrast-enhanced MRI"],
  "evidence_span": "brain tumor",
  "intent_confidence": 0.98,
  "rationale": "'Contrast-enhanced' describes the imaging acquisition, not the mask boundary. Removing it does not change which voxels are included — it is contextual."
}}

### Example D — non-enhancing subregion
Instruction: "Extract radiomics from the non-enhancing tumor region."
{{
  "target_id": "non_enhancing_brain_tumor",
  "canonical_label": "non-enhancing brain tumor",
  "anatomy": "brain",
  "pathology": "tumor",
  "scope": "lesion_subregion",
  "subregion": "non-enhancing component",
  "laterality": "unspecified",
  "target_defining_modifiers": ["non-enhancing"],
  "contextual_descriptors": [],
  "evidence_span": "non-enhancing tumor region",
  "intent_confidence": 0.99,
  "rationale": "Removing 'non-enhancing' would select the whole tumor or the enhancing compartment instead — it is target-defining."
}}

## Output format

Return one JSON object only:
{{
  "targets": [
    {{
      "target_id": "stable_lowercase_slug",
      "canonical_label": "compact target noun label",
      "anatomy": "base anatomical site",
      "pathology": "base pathological entity or null",
      "scope": "whole_organ|whole_lesion|lesion_subregion|tissue_region",
      "subregion": "named compartment or null",
      "laterality": "left|right|bilateral|unspecified",
      "target_defining_modifiers": ["modifier_word"],
      "contextual_descriptors": ["study context phrase"],
      "evidence_span": "verbatim span from the instruction",
      "intent_confidence": 0.0,
      "rationale": "why this is the requested physical ROI"
    }}
  ]
}}

Do not discuss models, masks, cache entries, or execution paths. Output JSON only.
"""

# ---------------------------------------------------------------------------
# Agent 2: Mask and VoxTell Resolution Agent
# ---------------------------------------------------------------------------

CANDIDATE_SYSTEM_MESSAGE = f"""
You are MaskResolutionAgent. You receive a locked clinical target identity from
Agent 1 and must decide:
  1. Which existing mask candidates are clinically equivalent to the target?
  2. Is there an exact VoxTell cache hit?
  3. If not, generate the exact locked canonical label as the prompt.

Apply the candidate-equivalence rules in this policy:

{CLINICAL_TARGET_POLICY}

## Your constraints

- Your output schema has NO anatomy, pathology, scope, subregion, laterality,
  or canonical_label fields. Those are locked by Agent 1 and must not change.
- Never invent target IDs. Use only the exact locked target IDs.
- The model and execution path are selected by the system, not by you. Do not
  include selected_model or chosen_path in your output.

## Mask candidate acceptance rules (Phase 1 — unstructured labels)

For each mask candidate, compare its display_label to the locked target:
- Accept if the display_label clearly preserves the complete target identity,
  including any target-defining modifier.
- Reject if the label is broader (missing a target-defining modifier).
- Reject if the label is narrower or selects a different subregion.
- Reject if the anatomy, pathology, or laterality differs.

Examples for locked target "enhancing brain tumor":
  display_label "enhancing brain tumor" → accept
  display_label "brain tumor" → reject (broader_than_target — missing "enhancing")
  display_label "non-enhancing brain tumor" → reject (different_subregion)
  display_label "brain" → reject (different_pathology)

Examples for locked target "spleen":
  display_label "spleen" → accept
  display_label "splenic lesion" → reject (different_pathology)
  display_label "spleen tumor" → reject (different_pathology)

## VoxTell cache rules

Cache mode: select only when the cache prompt **exactly** equals the locked
canonical_label (case-insensitive, whitespace-normalized). A longer or
differently worded prompt is not eligible — generate the exact canonical label
instead.

A cache miss is not a failure. Do not widen the canonical target to achieve a
cache hit.

## Confidence

match_confidence reflects your certainty that accepted mask candidates are
clinically equivalent to the locked target. If below
{REUSE_CONFIDENCE_THRESHOLD:.2f}, set accepted_mask_candidate_ids to [] and
use generate mode.

## Output format

Return one JSON object only:
{{
  "decisions": [
    {{
      "target_id": "exact locked target ID",
      "accepted_mask_candidate_ids": ["mask_candidate_id"],
      "rejected_mask_candidates": [
        {{
          "candidate_id": "mask_candidate_id",
          "relation": "broader_than_target|narrower_than_target|different_subregion|different_pathology|different_anatomy|different_laterality|insufficient_metadata",
          "reason": "why rejected"
        }}
      ],
      "voxtell_prompt": {{
        "mode": "cache",
        "cache_candidate_id": "cache_candidate_id",
        "prompt": null
      }},
      "match_confidence": 0.0,
      "rationale": "candidate equivalence reasoning"
    }}
  ]
}}

For generate mode use:
{{"mode": "generate", "cache_candidate_id": null, "prompt": "exact locked canonical label"}}

Output JSON only.
"""


def create_target_intent_agent(model_client):
    return AssistantAgent(
        name="ClinicalTargetIntentAgent",
        model_client=model_client,
        system_message=INTENT_SYSTEM_MESSAGE,
    )


def create_candidate_resolution_agent(model_client, discovery_tools):
    return AssistantAgent(
        name="MaskResolutionAgent",
        model_client=model_client,
        tools=discovery_tools,
        system_message=CANDIDATE_SYSTEM_MESSAGE,
        max_tool_iterations=10,
    )


def _extract_json(content: str) -> dict:
    text = str(content or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    return json.loads(text)


async def _run_validated_stage(
    agent,
    task: str,
    validator: Callable[[dict], ValidatedPlan],
    stage_name: str,
) -> ValidatedPlan:
    errors: list[str] = []
    for attempt in range(2):
        retry_instruction = ""
        if errors:
            retry_instruction = (
                "\n\nYour previous response was invalid: "
                f"{errors[-1]}. Return a corrected JSON object only."
            )
        result = await agent.run(task=f"{task}{retry_instruction}")
        try:
            return validator(_extract_json(result.messages[-1].content))
        except (ValueError, ValidationError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
            logger.warning(
                "%s output validation failed on attempt %d: %s",
                stage_name,
                attempt + 1,
                exc,
            )

    raise RuntimeError(
        f"{stage_name} failed to return valid output after one retry: "
        + " | ".join(errors)
    )


def _compose_resolution_plan(
    intent_plan: ClinicalTargetIntentPlan,
    candidate_plan: TargetCandidateDecisionPlan,
    chosen_path: str,
    selected_models: list[str],
) -> TargetResolutionPlan:
    decision_by_id = {
        decision.target_id: decision for decision in candidate_plan.decisions
    }
    targets: list[ResolvedTarget] = []

    for intent in intent_plan.targets:
        decision = decision_by_id[intent.target_id]
        targets.append(
            ResolvedTarget(
                target_id=intent.target_id,
                canonical_label=intent.canonical_label,
                inference_prompt=intent.canonical_label,  # equality enforced by validator
                anatomy=intent.anatomy,
                pathology=intent.pathology,
                scope=intent.scope,
                subregion=intent.subregion,
                laterality=intent.laterality,
                target_defining_modifiers=intent.target_defining_modifiers,
                accepted_mask_candidate_ids=decision.accepted_mask_candidate_ids,
                voxtell_prompt=decision.voxtell_prompt,
                intent_confidence=intent.intent_confidence,
                match_confidence=decision.match_confidence,
                reason=_compose_resolution_reason(
                    intent.rationale,
                    decision.rationale,
                ),
            )
        )

    return TargetResolutionPlan(
        chosen_path=chosen_path,
        selected_models=selected_models,
        targets=targets,
    )


def _truncate_reason_part(value: str, budget: int) -> str:
    value = value.strip()
    if len(value) <= budget:
        return value
    if budget <= 3:
        return "." * budget
    return value[: budget - 3].rstrip() + "..."


def _compose_resolution_reason(
    intent_rationale: str,
    candidate_rationale: str,
) -> str:
    """Combine both rationales without violating ResolvedTarget's contract."""
    intent_prefix = "Clinical intent: "
    candidate_prefix = "Candidate resolution: "
    separator = " "
    intent_rationale = intent_rationale.strip()
    candidate_rationale = candidate_rationale.strip()
    complete = (
        f"{intent_prefix}{intent_rationale}{separator}"
        f"{candidate_prefix}{candidate_rationale}"
    )
    if len(complete) <= RESOLVED_REASON_MAX_LENGTH:
        return complete

    available = RESOLVED_REASON_MAX_LENGTH - len(intent_prefix) - len(
        candidate_prefix
    ) - len(separator)
    intent_budget = available // 2
    candidate_budget = available - intent_budget

    if len(intent_rationale) < intent_budget:
        candidate_budget += intent_budget - len(intent_rationale)
        intent_budget = len(intent_rationale)
    if len(candidate_rationale) < candidate_budget:
        intent_budget += candidate_budget - len(candidate_rationale)
        candidate_budget = len(candidate_rationale)

    return (
        f"{intent_prefix}"
        f"{_truncate_reason_part(intent_rationale, intent_budget)}"
        f"{separator}{candidate_prefix}"
        f"{_truncate_reason_part(candidate_rationale, candidate_budget)}"
    )


def _enforce_execution_path(
    candidate_plan: TargetCandidateDecisionPlan,
) -> tuple[str, list[str]]:
    """Return (chosen_path, selected_models) determined deterministically by code.

    With a single enabled model there is no routing decision for the LLM to
    make. Normalize deterministically so a hallucinated name can never reach
    MONAI.
    """
    enabled = get_enabled_segmentation_model_paths()
    if not enabled:
        raise ValueError("No segmentation models are enabled")

    if len(enabled) == 1:
        model_name, execution_path = next(iter(enabled.items()))
        selected_models = [model_name] * len(candidate_plan.decisions)
        return execution_path, selected_models

    # Multiple models: each decision must name a known model; the chosen path
    # must be consistent. (Future-proofing: currently only one model exists.)
    enabled_by_key = {
        name.casefold(): (name, execution_path)
        for name, execution_path in enabled.items()
    }
    chosen_path: str | None = None
    selected_models: list[str] = []
    for decision in candidate_plan.decisions:
        # With the new schema Agent 2 does not pick a model; default to the
        # first enabled model if the field is absent.
        model_key = next(iter(enabled_by_key))
        canonical_name, execution_path = enabled_by_key[model_key]
        if chosen_path is None:
            chosen_path = execution_path
        elif chosen_path != execution_path:
            raise ValueError(
                f"Model {canonical_name!r} requires execution path "
                f"{execution_path!r}, not {chosen_path!r}"
            )
        selected_models.append(canonical_name)
    return chosen_path or "interactive", selected_models


def _candidate_catalog(
    mask_candidates: list[MaskLabelCandidate],
    cache_candidates: list[CachePromptCandidate],
) -> str:
    return json.dumps(
        {
            "existing_masks": [
                candidate.model_dump() for candidate in mask_candidates
            ],
            "voxtell_cache_prompts": [
                candidate.model_dump() for candidate in cache_candidates
            ],
        },
        indent=2,
    )


async def run_planner(
    intent_agent,
    candidate_agent,
    instruction: str,
    mask_candidates: list[MaskLabelCandidate] | None = None,
    cache_candidates: list[CachePromptCandidate] | None = None,
    locked_roi_interpretation: dict | None = None,
) -> TargetResolutionOutcome:
    mask_candidates = mask_candidates or []
    cache_candidates = cache_candidates or []

    # If the Clinical Planner locked a structured ROI interpretation, pass it
    # as authoritative context so Agent 1 does not re-derive scope from scratch.
    roi_preamble = ""
    if locked_roi_interpretation:
        roi_preamble = (
            "\nLOCKED ROI INTERPRETATION (from Clinical Planner — treat as "
            "authoritative; do not override it):\n"
            + json.dumps(locked_roi_interpretation, indent=2)
            + "\n"
        )

    intent_task = f"""
Resolve all segmentation ROI targets requested by this clinical instruction.
{roi_preamble}
CLINICAL INSTRUCTION:
{instruction}
"""
    intent_plan = await _run_validated_stage(
        intent_agent,
        intent_task,
        ClinicalTargetIntentPlan.model_validate,
        "Clinical target intent agent",
    )
    logger.info(
        "Clinical target intent plan: %s",
        intent_plan.model_dump_json(),
    )

    candidate_task = f"""
Choose mask candidates and VoxTell prompt resolution for this locked clinical
intent. Do NOT change target identity.

LOCKED CLINICAL INTENT:
{intent_plan.model_dump_json(indent=2)}

ACTUAL CANDIDATES:
{_candidate_catalog(mask_candidates, cache_candidates)}

Candidate IDs must come only from these lists. An empty cache list means the
inventory is unavailable; use generate mode with the exact locked canonical
label for each target.
"""

    def validate_candidate_plan(raw: dict) -> TargetCandidateDecisionPlan:
        candidate_plan = TargetCandidateDecisionPlan.model_validate(raw)
        return validate_candidate_decision_references(
            candidate_plan,
            intent_plan,
            mask_candidates,
            cache_candidates,
        )

    candidate_plan = await _run_validated_stage(
        candidate_agent,
        candidate_task,
        validate_candidate_plan,
        "Mask resolution agent",
    )
    logger.info(
        "Target candidate decision plan: %s",
        candidate_plan.model_dump_json(),
    )

    # Code — not Agent 2 — selects the execution path and model.
    chosen_path, selected_models = _enforce_execution_path(candidate_plan)

    proposed_plan = _compose_resolution_plan(
        intent_plan, candidate_plan, chosen_path, selected_models
    )
    validate_candidate_references(
        proposed_plan, mask_candidates, cache_candidates
    )
    plan = proposed_plan

    prompt_by_cache_id = {
        candidate.candidate_id: candidate.prompt
        for candidate in cache_candidates
    }
    resolved_prompts = {
        target.target_id: (
            target.inference_prompt
            if target.voxtell_prompt.mode == "generate"
            else prompt_by_cache_id[
                str(target.voxtell_prompt.cache_candidate_id)
            ]
        )
        for target in plan.targets
    }
    logger.info(
        "Final target resolution labels=%s prompts=%s policy_fingerprint=%s",
        plan.target_labels,
        resolved_prompts,
        CLINICAL_TARGET_POLICY_FINGERPRINT,
    )

    return TargetResolutionOutcome(
        plan=plan,
        intent_plan=intent_plan,
        candidate_plan=candidate_plan,
        policy_fingerprint=CLINICAL_TARGET_POLICY_FINGERPRINT,
    )
