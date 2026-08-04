import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator


REUSE_CONFIDENCE_THRESHOLD = 0.6
RESOLVED_REASON_MAX_LENGTH = 2048


# VoxTell works best with a compact target noun label. These terms describe
# image appearance rather than the anatomical/pathological identity used to
# select the ROI. Keep this deliberately narrow: clinical subtype words must
# not be removed by a generic stop-word list.
_ENHANCEMENT_DESCRIPTION_RE = re.compile(
    r"\b(?:contrast[\s-]+)?(?:non[\s-]*)?enhanc(?:ed|ing)\b",
    flags=re.IGNORECASE,
)


def _strip_inference_description(value: str) -> str:
    value = _ENHANCEMENT_DESCRIPTION_RE.sub(" ", value)
    return " ".join(value.split()).strip(" -_,")


def _base_pathology_label(
    pathology: str,
    anatomy: str,
    laterality: str,
) -> str:
    """Remove anatomy repeated inside the structured pathology field."""
    pathology = _strip_inference_description(pathology)
    side = "" if laterality == "unspecified" else laterality
    anatomy_forms = {
        anatomy,
        " ".join(part for part in (side, anatomy) if part),
    }
    for anatomy_form in sorted(anatomy_forms, key=len, reverse=True):
        for article in ("the ", ""):
            suffix = f" of {article}{anatomy_form}"
            if pathology.casefold().endswith(suffix.casefold()):
                return pathology[: -len(suffix)].strip()
    return pathology


def _canonicalize_target_label(
    label: str,
    anatomy: str,
    pathology: str | None,
    laterality: str,
) -> str:
    """Return a compact VoxTell target label without imaging description."""
    label = _strip_inference_description(label)
    if not pathology:
        return label

    # Naturalize LLM phrases such as "tumor of brain" to the anatomy-first
    # target name expected by VoxTell. This is structural normalization using
    # the already-resolved identity fields, not runtime clinical synonym
    # matching.
    side = "" if laterality == "unspecified" else laterality
    anatomy_with_side = " ".join(part for part in (side, anatomy) if part)
    inverted_forms = {
        f"{pathology} of {anatomy}".casefold(),
        f"{pathology} of the {anatomy}".casefold(),
        f"{pathology} of {anatomy_with_side}".casefold(),
        f"{pathology} of the {anatomy_with_side}".casefold(),
    }
    if label.casefold() in inverted_forms:
        return " ".join(part for part in (side, anatomy, pathology) if part)
    return label


class ClinicalTargetIntent(BaseModel):
    target_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")
    canonical_label: str = Field(min_length=1, max_length=200)
    anatomy: str = Field(min_length=1, max_length=120)
    pathology: str | None = Field(default=None, max_length=120)
    laterality: Literal["left", "right", "bilateral", "unspecified"] = "unspecified"
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def normalize_clinical_identity(self):
        self.anatomy = self.anatomy.strip()
        if self.pathology:
            self.pathology = _base_pathology_label(
                self.pathology,
                self.anatomy,
                self.laterality,
            )
            if not self.pathology:
                raise ValueError(
                    "Pathology must contain a base target entity, not only "
                    "imaging description"
                )
        self.canonical_label = _canonicalize_target_label(
            self.canonical_label.strip(),
            self.anatomy,
            self.pathology,
            self.laterality,
        )
        self.rationale = self.rationale.strip()
        if not self.canonical_label or not self.anatomy or not self.rationale:
            raise ValueError(
                "Clinical target identity fields cannot contain only whitespace"
            )
        return self


class ClinicalTargetIntentPlan(BaseModel):
    targets: list[ClinicalTargetIntent] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_targets(self):
        target_ids = [target.target_id for target in self.targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("target_id values must be unique")
        if any("__" in target_id for target_id in target_ids):
            raise ValueError("target_id values cannot contain the reserved '__' delimiter")
        return self


class MaskLabelCandidate(BaseModel):
    candidate_id: str
    display_label: str
    structured: bool


class CachePromptCandidate(BaseModel):
    candidate_id: str
    prompt: str


class VoxTellPromptDecision(BaseModel):
    mode: Literal["cache", "generate"]
    cache_candidate_id: str | None = None
    prompt: str | None = None

    @model_validator(mode="after")
    def validate_mode_fields(self):
        if self.mode == "cache":
            if not self.cache_candidate_id or self.prompt:
                raise ValueError(
                    "cache mode requires cache_candidate_id and forbids prompt"
                )
        elif not self.prompt or self.cache_candidate_id:
            raise ValueError(
                "generate mode requires prompt and forbids cache_candidate_id"
            )
        return self


class TargetCandidateDecision(BaseModel):
    target_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")
    accepted_mask_candidate_ids: list[str] = Field(default_factory=list)
    voxtell_prompt: VoxTellPromptDecision
    selected_model: str = Field(min_length=1, max_length=120)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_candidate_confidence(self):
        if len(self.accepted_mask_candidate_ids) != len(
            set(self.accepted_mask_candidate_ids)
        ):
            raise ValueError("accepted_mask_candidate_ids must be unique")
        if self.confidence < REUSE_CONFIDENCE_THRESHOLD:
            if self.accepted_mask_candidate_ids:
                raise ValueError(
                    "Low-confidence candidate decisions cannot reuse masks"
                )
            if self.voxtell_prompt.mode != "generate":
                raise ValueError(
                    "Low-confidence candidate decisions must generate a prompt"
                )
        self.selected_model = self.selected_model.strip()
        self.rationale = self.rationale.strip()
        if not self.selected_model or not self.rationale:
            raise ValueError(
                "Candidate model and rationale cannot contain only whitespace"
            )
        return self


class TargetCandidateDecisionPlan(BaseModel):
    chosen_path: Literal["automatic", "interactive"]
    decisions: list[TargetCandidateDecision] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_targets(self):
        target_ids = [decision.target_id for decision in self.decisions]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("target_id values must be unique")
        return self


class ResolvedTarget(BaseModel):
    target_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")
    canonical_label: str = Field(min_length=1, max_length=200)
    anatomy: str = Field(min_length=1, max_length=120)
    pathology: str | None = Field(default=None, max_length=120)
    laterality: Literal["left", "right", "bilateral", "unspecified"] = "unspecified"
    accepted_mask_candidate_ids: list[str] = Field(default_factory=list)
    voxtell_prompt: VoxTellPromptDecision
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=RESOLVED_REASON_MAX_LENGTH)

    @model_validator(mode="after")
    def generated_prompt_is_canonical(self):
        if self.voxtell_prompt.mode == "generate":
            prompt = str(self.voxtell_prompt.prompt or "").strip()
            canonical = self.canonical_label.strip()
            if prompt.casefold() != canonical.casefold():
                raise ValueError(
                    "A generated VoxTell prompt must equal canonical_label"
                )
            self.voxtell_prompt.prompt = canonical
        self.canonical_label = self.canonical_label.strip()
        if self.pathology:
            self.pathology = self.pathology.strip()
        if self.confidence < REUSE_CONFIDENCE_THRESHOLD:
            if self.accepted_mask_candidate_ids:
                raise ValueError(
                    "Low-confidence targets cannot reuse existing masks"
                )
            if self.voxtell_prompt.mode != "generate":
                raise ValueError(
                    "Low-confidence targets must generate the exact canonical prompt"
                )
        return self


class TargetResolutionPlan(BaseModel):
    chosen_path: Literal["automatic", "interactive"]
    selected_models: list[str] = Field(min_length=1)
    targets: list[ResolvedTarget] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_targets(self):
        target_ids = [target.target_id for target in self.targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("target_id values must be unique")
        if len(self.selected_models) not in (1, len(self.targets)):
            raise ValueError(
                "selected_models must contain one shared model or one model "
                "per target"
            )
        return self

    @property
    def target_labels(self) -> list[str]:
        return [target.canonical_label for target in self.targets]


class ClinicalPlanAudit(BaseModel):
    approved: bool
    clinical_identity_invalid: bool = False
    issues: list[str] = Field(default_factory=list)
    corrected_plan: TargetResolutionPlan | None = None

    @model_validator(mode="after")
    def validate_audit_decision(self):
        if self.approved:
            if self.clinical_identity_invalid:
                raise ValueError(
                    "An approved audit cannot mark clinical identity invalid"
                )
            if self.corrected_plan is not None:
                raise ValueError("An approved audit must not return corrected_plan")
        if not self.approved:
            if not self.issues:
                raise ValueError("A rejected audit must explain at least one issue")
            if self.clinical_identity_invalid and self.corrected_plan is not None:
                raise ValueError(
                    "An invalid locked clinical identity must not be silently replaced"
                )
            if not self.clinical_identity_invalid and self.corrected_plan is None:
                raise ValueError("A rejected audit must return corrected_plan")
        return self


class TargetResolutionOutcome(BaseModel):
    plan: TargetResolutionPlan
    intent_plan: ClinicalTargetIntentPlan
    candidate_plan: TargetCandidateDecisionPlan
    audit: ClinicalPlanAudit
    policy_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


def validate_candidate_references(
    plan: TargetResolutionPlan,
    mask_candidates: list[MaskLabelCandidate],
    cache_candidates: list[CachePromptCandidate],
) -> TargetResolutionPlan:
    mask_ids = {candidate.candidate_id for candidate in mask_candidates}
    cache_ids = {candidate.candidate_id for candidate in cache_candidates}
    cache_by_id = {
        candidate.candidate_id: candidate for candidate in cache_candidates
    }

    for target in plan.targets:
        unknown_masks = set(target.accepted_mask_candidate_ids) - mask_ids
        if unknown_masks:
            raise ValueError(
                f"Target {target.target_id!r} returned unknown mask candidate IDs: "
                f"{sorted(unknown_masks)}"
            )
        if target.voxtell_prompt.mode == "cache":
            candidate_id = str(target.voxtell_prompt.cache_candidate_id)
            if candidate_id not in cache_ids:
                raise ValueError(
                    f"Target {target.target_id!r} returned unknown cache candidate "
                    f"ID: {candidate_id}"
                )
            cached_prompt = cache_by_id[candidate_id].prompt.strip()
            if cached_prompt.casefold() != target.canonical_label.casefold():
                raise ValueError(
                    f"VoxTell cache prompt for target {target.target_id!r} must "
                    "exactly match its compact canonical label"
                )
    return plan


def validate_candidate_decision_references(
    plan: TargetCandidateDecisionPlan,
    intent_plan: ClinicalTargetIntentPlan,
    mask_candidates: list[MaskLabelCandidate],
    cache_candidates: list[CachePromptCandidate],
) -> TargetCandidateDecisionPlan:
    expected_target_ids = {target.target_id for target in intent_plan.targets}
    returned_target_ids = {decision.target_id for decision in plan.decisions}
    if returned_target_ids != expected_target_ids:
        missing = sorted(expected_target_ids - returned_target_ids)
        unknown = sorted(returned_target_ids - expected_target_ids)
        raise ValueError(
            "Candidate decisions must correspond exactly to locked intent "
            f"targets; missing={missing}, unknown={unknown}"
        )

    mask_ids = {candidate.candidate_id for candidate in mask_candidates}
    cache_ids = {candidate.candidate_id for candidate in cache_candidates}
    cache_by_id = {
        candidate.candidate_id: candidate for candidate in cache_candidates
    }
    intent_by_id = {
        target.target_id: target for target in intent_plan.targets
    }

    for decision in plan.decisions:
        unknown_masks = set(decision.accepted_mask_candidate_ids) - mask_ids
        if unknown_masks:
            raise ValueError(
                f"Target {decision.target_id!r} returned unknown mask candidate "
                f"IDs: {sorted(unknown_masks)}"
            )
        if decision.voxtell_prompt.mode == "cache":
            candidate_id = str(decision.voxtell_prompt.cache_candidate_id)
            if candidate_id not in cache_ids:
                raise ValueError(
                    f"Target {decision.target_id!r} returned unknown cache "
                    f"candidate ID: {candidate_id}"
                )
            cached_prompt = cache_by_id[candidate_id].prompt.strip()
            canonical = intent_by_id[decision.target_id].canonical_label
            if cached_prompt.casefold() != canonical.casefold():
                raise ValueError(
                    f"VoxTell cache prompt for target {decision.target_id!r} "
                    "must exactly match its compact canonical label"
                )
        else:
            prompt = str(decision.voxtell_prompt.prompt or "").strip()
            canonical = intent_by_id[decision.target_id].canonical_label
            if prompt.casefold() != canonical.casefold():
                raise ValueError(
                    "A generated VoxTell prompt must equal the locked canonical "
                    f"label for target {decision.target_id!r}"
                )
            decision.voxtell_prompt.prompt = canonical
    return plan


def validate_audited_plan_identity(
    plan: TargetResolutionPlan,
    intent_plan: ClinicalTargetIntentPlan,
) -> TargetResolutionPlan:
    """Prevent the audit stage from rewriting the locked clinical target."""
    expected = {target.target_id: target for target in intent_plan.targets}
    returned = {target.target_id: target for target in plan.targets}
    if returned.keys() != expected.keys():
        raise ValueError(
            "An audited correction must preserve the locked target IDs exactly"
        )

    for target_id, intent in expected.items():
        resolved = returned[target_id]
        identity_fields = {
            "canonical_label": (
                resolved.canonical_label.casefold(),
                intent.canonical_label.casefold(),
            ),
            "anatomy": (resolved.anatomy.casefold(), intent.anatomy.casefold()),
            "pathology": (
                (resolved.pathology or "").casefold(),
                (intent.pathology or "").casefold(),
            ),
            "laterality": (resolved.laterality, intent.laterality),
        }
        changed = [
            field
            for field, (actual, locked) in identity_fields.items()
            if actual != locked
        ]
        if changed:
            raise ValueError(
                f"Audited correction changed locked clinical identity for "
                f"{target_id!r}: {changed}. Clinical intent issues must fail "
                "safely instead of being rewritten in the candidate audit."
            )
    return plan
