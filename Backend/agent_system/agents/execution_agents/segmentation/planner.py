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
    ClinicalPlanAudit,
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
    validate_audited_plan_identity,
)
from .model_catalog import get_enabled_segmentation_model_paths

logger = logging.getLogger(__name__)
ValidatedPlan = TypeVar("ValidatedPlan")

INTENT_SYSTEM_MESSAGE = f"""
You are ClinicalTargetIntentAgent. Determine the clinical ROI identity requested
for medical-image segmentation. You must decide target identity before any
existing mask labels, cache prompts, or available model names are considered.

Apply this clinician-reviewable policy:

{CLINICAL_TARGET_POLICY}

Distinguish the ROI from the study outcome. If radiomics/features are intended
to classify a diseased or cancerous organ and no explicit whole-organ boundary
is requested, the ROI is pathological tissue in that organ, not the healthy
organ label. Explicit "whole", "entire", or "organ mask" language requests the
whole organ. Disease that only describes the patient cohort does not change an
explicit organ ROI.

Return one JSON object only:
{{
  "targets": [
    {{
      "target_id": "stable_lowercase_slug",
      "canonical_label": "precise clinical target",
      "anatomy": "anatomical site",
      "pathology": "pathology or null",
      "laterality": "left|right|bilateral|unspecified",
      "confidence": 0.0,
      "rationale": "why this is the requested ROI rather than study context"
    }}
  ]
}}

Resolve all requested targets. Do not discuss models, masks, cache entries, or
execution paths. Output JSON only.
"""

CANDIDATE_SYSTEM_MESSAGE = f"""
You are TargetCandidateResolutionAgent. You receive a locked clinical target
intent plus actual existing-mask and VoxTell cache candidates. Select execution
and reuse choices without changing clinical identity.

Apply the candidate-equivalence rules in this policy:

{CLINICAL_TARGET_POLICY}

Your output schema intentionally contains no canonical label, anatomy,
pathology, or laterality fields. Never invent target IDs. For each locked target
ID:
- accept only mask candidate IDs that are clinically equivalent in anatomy,
  pathology, scope, and laterality;
- select a cache candidate only when its prompt is clinically equivalent;
- otherwise generate the exact locked canonical label;
- choose an available segmentation model and automatic or interactive path;
- call the model discovery tool and select only a model it returns;
- never invent, infer, or rename a model;
- if candidate-equivalence confidence is below
  {REUSE_CONFIDENCE_THRESHOLD:.2f}, reuse no mask/cache candidate.

Return one JSON object only:
{{
  "chosen_path": "interactive" | "automatic",
  "decisions": [
    {{
      "target_id": "an exact locked target ID",
      "accepted_mask_candidate_ids": ["mask_candidate_id"],
      "voxtell_prompt": {{
        "mode": "cache",
        "cache_candidate_id": "cache_candidate_id",
        "prompt": null
      }},
      "selected_model": "model name",
      "confidence": 0.0,
      "rationale": "candidate equivalence and execution reasoning"
    }}
  ]
}}

For generation, use:
{{"mode": "generate", "cache_candidate_id": null,
  "prompt": "exact locked canonical label"}}

Output JSON only.
"""

AUDITOR_SYSTEM_MESSAGE = f"""
You are IndependentClinicalTargetAuditor. You did not participate in clinical
intent detection or candidate selection. Re-read the original instruction and
audit the complete proposed plan independently.

Apply this clinician-reviewable policy:

{CLINICAL_TARGET_POLICY}

Audit disease/pathology versus healthy-organ intent, whole-organ scope, disease
used only as cohort/outcome/background context, anatomy, laterality, mask/cache
equivalence, prompt precision, execution path, and selected model.

Critical decisions:
- "classify cancerous spleens" requires a pathological spleen target and must
  not resolve to canonical_label "spleen";
- "segment splenic lesion" requires a lesion target;
- "segment spleen in patients with cancer" requests the whole spleen;
- "segment the whole cancerous spleen" requests the whole spleen.
- Model names must come from the supplied execution plan and model discovery
  results. Never invent or rename a model.

Return JSON only:
{{
  "approved": true,
  "clinical_identity_invalid": false,
  "issues": [],
  "corrected_plan": null
}}

If any issue exists, return approved=false, list the issues, and return a
complete corrected TargetResolutionPlan in corrected_plan. Use only supplied
candidate IDs. If candidate equivalence is uncertain, reuse nothing and
generate the exact locked canonical prompt. A corrected plan may change
candidate reuse or execution choices, but it MUST preserve every locked target
ID, canonical label, anatomy, pathology, and laterality. If the locked clinical
identity itself is wrong, set clinical_identity_invalid=true, reject it in
issues, and return corrected_plan=null without inventing a replacement.
"""


def create_target_intent_agent(model_client):
    return AssistantAgent(
        name="ClinicalTargetIntentAgent",
        model_client=model_client,
        system_message=INTENT_SYSTEM_MESSAGE,
    )


def create_candidate_resolution_agent(model_client, discovery_tools):
    return AssistantAgent(
        name="TargetCandidateResolutionAgent",
        model_client=model_client,
        tools=discovery_tools,
        system_message=CANDIDATE_SYSTEM_MESSAGE,
        max_tool_iterations=10,
    )


def create_target_auditor_agent(model_client):
    return AssistantAgent(
        name="IndependentClinicalTargetAuditor",
        model_client=model_client,
        system_message=AUDITOR_SYSTEM_MESSAGE,
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
) -> TargetResolutionPlan:
    decision_by_id = {
        decision.target_id: decision for decision in candidate_plan.decisions
    }
    targets: list[ResolvedTarget] = []
    selected_models: list[str] = []

    for intent in intent_plan.targets:
        decision = decision_by_id[intent.target_id]
        targets.append(
            ResolvedTarget(
                target_id=intent.target_id,
                canonical_label=intent.canonical_label,
                anatomy=intent.anatomy,
                pathology=intent.pathology,
                laterality=intent.laterality,
                accepted_mask_candidate_ids=decision.accepted_mask_candidate_ids,
                voxtell_prompt=decision.voxtell_prompt,
                confidence=min(intent.confidence, decision.confidence),
                reason=_compose_resolution_reason(
                    intent.rationale,
                    decision.rationale,
                ),
            )
        )
        selected_models.append(decision.selected_model)

    return TargetResolutionPlan(
        chosen_path=candidate_plan.chosen_path,
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


def _enforce_candidate_execution(
    plan: TargetCandidateDecisionPlan,
) -> TargetCandidateDecisionPlan:
    enabled = get_enabled_segmentation_model_paths()
    if not enabled:
        raise ValueError("No segmentation models are enabled")

    # With one deployed model there is no routing decision for the LLM to make.
    # Normalize deterministically so a hallucinated name can never reach MONAI.
    if len(enabled) == 1:
        model_name, execution_path = next(iter(enabled.items()))
        returned_models = [decision.selected_model for decision in plan.decisions]
        if (
            plan.chosen_path != execution_path
            or any(
                name.casefold() != model_name.casefold()
                for name in returned_models
            )
        ):
            logger.warning(
                "Normalized candidate execution from path=%s models=%s to "
                "path=%s model=%s",
                plan.chosen_path,
                returned_models,
                execution_path,
                model_name,
            )
        plan.chosen_path = execution_path
        for decision in plan.decisions:
            decision.selected_model = model_name
        return plan

    enabled_by_key = {
        name.casefold(): (name, execution_path)
        for name, execution_path in enabled.items()
    }
    for decision in plan.decisions:
        selected = enabled_by_key.get(decision.selected_model.casefold())
        if selected is None:
            raise ValueError(
                f"Unknown segmentation model {decision.selected_model!r}; "
                f"available models are {sorted(enabled)}"
            )
        canonical_name, execution_path = selected
        if plan.chosen_path != execution_path:
            raise ValueError(
                f"Model {canonical_name!r} requires execution path "
                f"{execution_path!r}, not {plan.chosen_path!r}"
            )
        decision.selected_model = canonical_name
    return plan


def _enforce_resolution_execution(
    plan: TargetResolutionPlan,
) -> TargetResolutionPlan:
    enabled = get_enabled_segmentation_model_paths()
    if not enabled:
        raise ValueError("No segmentation models are enabled")

    if len(enabled) == 1:
        model_name, execution_path = next(iter(enabled.items()))
        if (
            plan.chosen_path != execution_path
            or any(
                name.casefold() != model_name.casefold()
                for name in plan.selected_models
            )
        ):
            logger.warning(
                "Normalized audited execution from path=%s models=%s to "
                "path=%s model=%s",
                plan.chosen_path,
                plan.selected_models,
                execution_path,
                model_name,
            )
        plan.chosen_path = execution_path
        plan.selected_models = [model_name]
        return plan

    enabled_by_key = {
        name.casefold(): (name, execution_path)
        for name, execution_path in enabled.items()
    }
    canonical_models: list[str] = []
    for model_name in plan.selected_models:
        selected = enabled_by_key.get(model_name.casefold())
        if selected is None:
            raise ValueError(
                f"Unknown segmentation model {model_name!r}; "
                f"available models are {sorted(enabled)}"
            )
        canonical_name, execution_path = selected
        if plan.chosen_path != execution_path:
            raise ValueError(
                f"Model {canonical_name!r} requires execution path "
                f"{execution_path!r}, not {plan.chosen_path!r}"
            )
        canonical_models.append(canonical_name)
    plan.selected_models = canonical_models
    return plan


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
    auditor_agent,
    instruction: str,
    mask_candidates: list[MaskLabelCandidate] | None = None,
    cache_candidates: list[CachePromptCandidate] | None = None,
) -> TargetResolutionOutcome:
    mask_candidates = mask_candidates or []
    cache_candidates = cache_candidates or []

    intent_task = f"""
Resolve all segmentation ROI targets requested by this clinical instruction.

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
Choose candidates and execution settings for this locked clinical intent.

LOCKED CLINICAL INTENT:
{intent_plan.model_dump_json(indent=2)}

ACTUAL CANDIDATES:
{_candidate_catalog(mask_candidates, cache_candidates)}

Candidate IDs must come only from these lists. An empty cache list means the
inventory is unavailable; generate each exact locked canonical prompt.
"""

    def validate_candidate_plan(raw: dict) -> TargetCandidateDecisionPlan:
        candidate_plan = TargetCandidateDecisionPlan.model_validate(raw)
        candidate_plan = validate_candidate_decision_references(
            candidate_plan,
            intent_plan,
            mask_candidates,
            cache_candidates,
        )
        return _enforce_candidate_execution(candidate_plan)

    candidate_plan = await _run_validated_stage(
        candidate_agent,
        candidate_task,
        validate_candidate_plan,
        "Target candidate resolution agent",
    )
    logger.info(
        "Target candidate decision plan: %s",
        candidate_plan.model_dump_json(),
    )

    proposed_plan = _compose_resolution_plan(intent_plan, candidate_plan)
    validate_candidate_references(
        proposed_plan, mask_candidates, cache_candidates
    )

    audit_task = f"""
Independently audit this proposed target-resolution plan.

ORIGINAL CLINICAL INSTRUCTION:
{instruction}

PROPOSED COMPLETE PLAN:
{proposed_plan.model_dump_json(indent=2)}

ACTUAL CANDIDATES:
{_candidate_catalog(mask_candidates, cache_candidates)}
"""

    def validate_audit(raw: dict) -> ClinicalPlanAudit:
        audit = ClinicalPlanAudit.model_validate(raw)
        if audit.corrected_plan is not None:
            audit.corrected_plan = _enforce_resolution_execution(
                audit.corrected_plan
            )
            validate_audited_plan_identity(
                audit.corrected_plan,
                intent_plan,
            )
            validate_candidate_references(
                audit.corrected_plan,
                mask_candidates,
                cache_candidates,
            )
        return audit

    audit = await _run_validated_stage(
        auditor_agent,
        audit_task,
        validate_audit,
        "Independent target auditor",
    )
    if audit.clinical_identity_invalid:
        raise RuntimeError(
            "Independent target auditor rejected the locked clinical identity: "
            + "; ".join(audit.issues)
        )
    plan = proposed_plan if audit.approved else audit.corrected_plan
    if plan is None:
        raise RuntimeError("Independent target auditor returned no final plan")

    if not audit.approved:
        logger.warning(
            "Independent target auditor corrected the plan: %s",
            "; ".join(audit.issues),
        )
    logger.info("Independent target audit: %s", audit.model_dump_json())

    prompt_by_cache_id = {
        candidate.candidate_id: candidate.prompt
        for candidate in cache_candidates
    }
    resolved_prompts = {
        target.target_id: (
            target.voxtell_prompt.prompt
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
        audit=audit,
        policy_fingerprint=CLINICAL_TARGET_POLICY_FINGERPRINT,
    )
