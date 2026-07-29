import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Callable, TypeVar

from autogen_agentchat.agents import AssistantAgent
from pydantic import ValidationError

from .contracts import (
    CachePromptCandidate,
    ClinicalPlanAudit,
    ClinicalTargetIntentPlan,
    MaskLabelCandidate,
    ResolvedTarget,
    TargetCandidateDecisionPlan,
    TargetResolutionOutcome,
    TargetResolutionPlan,
    validate_candidate_decision_references,
    validate_candidate_references,
)

logger = logging.getLogger(__name__)
ValidatedPlan = TypeVar("ValidatedPlan")


def _load_clinical_target_policy() -> str:
    policy_path = Path(__file__).with_name("clinical_target_policy.md")
    try:
        return policy_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(
            f"Clinical target policy is unavailable at {policy_path}"
        ) from exc


CLINICAL_TARGET_POLICY = _load_clinical_target_policy()
CLINICAL_TARGET_POLICY_FINGERPRINT = hashlib.sha256(
    CLINICAL_TARGET_POLICY.encode("utf-8")
).hexdigest()

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
- if confidence is below 0.80, reuse no mask/cache candidate.

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

Return JSON only:
{{
  "approved": true,
  "issues": [],
  "corrected_plan": null
}}

If any issue exists, return approved=false, list the issues, and return a
complete corrected TargetResolutionPlan in corrected_plan. Use only supplied
candidate IDs. If candidate equivalence is uncertain, reuse nothing and
generate the exact corrected canonical prompt.
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
                reason=(
                    f"Clinical intent: {intent.rationale} "
                    f"Candidate resolution: {decision.rationale}"
                ),
            )
        )
        selected_models.append(decision.selected_model)

    return TargetResolutionPlan(
        chosen_path=candidate_plan.chosen_path,
        selected_models=selected_models,
        targets=targets,
    )


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
