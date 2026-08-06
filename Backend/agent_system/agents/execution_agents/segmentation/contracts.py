from typing import Literal

from pydantic import BaseModel, Field, model_validator


CURRENT_SCHEMA_VERSION = 2
REUSE_CONFIDENCE_THRESHOLD = 0.6
RESOLVED_REASON_MAX_LENGTH = 2048


# ---------------------------------------------------------------------------
# Agent 1 output: clinical target identity
# ---------------------------------------------------------------------------


class ClinicalTargetIntent(BaseModel):
    """Locked physical ROI identity produced by Agent 1.

    Agent 1 determines *what* region to delineate. It must not see existing
    masks, cache prompts, or model names so that the available inventory
    cannot bias the clinical interpretation.
    """

    target_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")
    canonical_label: str = Field(min_length=1, max_length=200)

    anatomy: str = Field(min_length=1, max_length=120)
    pathology: str | None = Field(default=None, max_length=120)

    scope: Literal[
        "whole_organ",
        "whole_lesion",
        "lesion_subregion",
        "tissue_region",
    ]

    subregion: str | None = Field(default=None, max_length=120)

    laterality: Literal["left", "right", "bilateral", "unspecified"] = "unspecified"

    # Words whose removal would change the voxel boundary, select a different
    # lesion, or collapse a separately delineated subregion into a broader ROI.
    target_defining_modifiers: list[str] = Field(default_factory=list)

    # Clinically important context that does not alter the mask boundary
    # (treatment history, study cohort, imaging rationale, etc.).
    contextual_descriptors: list[str] = Field(default_factory=list)

    # Verbatim span from the clinical instruction that establishes the target.
    evidence_span: str = Field(min_length=1, max_length=500)

    intent_confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_clinical_identity(self):
        self.anatomy = self.anatomy.strip()
        if self.pathology is not None:
            self.pathology = self.pathology.strip()
            if not self.pathology:
                raise ValueError("pathology cannot be blank when provided")
        self.canonical_label = self.canonical_label.strip()
        self.rationale = self.rationale.strip()
        self.evidence_span = self.evidence_span.strip()
        if not self.canonical_label or not self.anatomy or not self.rationale:
            raise ValueError(
                "canonical_label, anatomy, and rationale cannot be blank"
            )

        # A lesion subregion must name the subregion.
        if self.scope == "lesion_subregion" and not self.subregion:
            raise ValueError(
                "subregion is required when scope is 'lesion_subregion'"
            )

        # Every target-defining modifier must appear in the canonical label so
        # the agent cannot claim to preserve a word it actually dropped.
        canonical_lower = self.canonical_label.casefold()
        for modifier in self.target_defining_modifiers:
            if modifier.casefold() not in canonical_lower:
                raise ValueError(
                    f"target_defining_modifier {modifier!r} is not present in "
                    f"canonical_label {self.canonical_label!r}"
                )

        # Reject sentence-length labels: they are likely the raw instruction,
        # not a compact target noun phrase.
        if len(self.canonical_label.split()) > 8:
            raise ValueError(
                "canonical_label must be a compact target noun phrase, not a "
                "sentence (too many words)"
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
            raise ValueError(
                "target_id values cannot contain the reserved '__' delimiter"
            )
        return self


# ---------------------------------------------------------------------------
# Agent 2 input: candidate inventories
# ---------------------------------------------------------------------------


class MaskLabelCandidate(BaseModel):
    candidate_id: str
    display_label: str
    structured: bool


class CachePromptCandidate(BaseModel):
    candidate_id: str
    prompt: str


# ---------------------------------------------------------------------------
# Agent 2 output: mask/cache resolution decisions
# ---------------------------------------------------------------------------


class RejectedCandidate(BaseModel):
    """Explains why a specific mask candidate was not accepted."""

    candidate_id: str
    relation: Literal[
        "broader_than_target",
        "narrower_than_target",
        "different_subregion",
        "different_pathology",
        "different_anatomy",
        "different_laterality",
        "insufficient_metadata",
    ]
    reason: str = Field(min_length=1, max_length=500)


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
    """Agent 2 output for a single locked target.

    This schema intentionally omits anatomy, pathology, scope, subregion,
    laterality, and canonical_label — those fields are locked by Agent 1 and
    must not be re-interpreted here.
    """

    target_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")
    accepted_mask_candidate_ids: list[str] = Field(default_factory=list)
    rejected_mask_candidates: list[RejectedCandidate] = Field(default_factory=list)
    voxtell_prompt: VoxTellPromptDecision
    match_confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_candidate_confidence(self):
        if len(self.accepted_mask_candidate_ids) != len(
            set(self.accepted_mask_candidate_ids)
        ):
            raise ValueError("accepted_mask_candidate_ids must be unique")
        if self.match_confidence < REUSE_CONFIDENCE_THRESHOLD:
            if self.accepted_mask_candidate_ids:
                raise ValueError(
                    "Low-confidence candidate decisions cannot reuse masks"
                )
            if self.voxtell_prompt.mode != "generate":
                raise ValueError(
                    "Low-confidence candidate decisions must generate a prompt"
                )
        self.rationale = self.rationale.strip()
        if not self.rationale:
            raise ValueError("rationale cannot be blank")
        return self


class TargetCandidateDecisionPlan(BaseModel):
    decisions: list[TargetCandidateDecision] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_targets(self):
        target_ids = [decision.target_id for decision in self.decisions]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("target_id values must be unique")
        return self


# ---------------------------------------------------------------------------
# Composed resolution: merges Agent 1 + Agent 2 outputs
# ---------------------------------------------------------------------------


class ResolvedTarget(BaseModel):
    """Final resolved target carrying both intent identity and execution plan."""

    target_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")
    canonical_label: str = Field(min_length=1, max_length=200)

    # Clinical identity (locked from Agent 1, never modified by Agent 2).
    anatomy: str = Field(min_length=1, max_length=120)
    pathology: str | None = Field(default=None, max_length=120)
    scope: Literal[
        "whole_organ",
        "whole_lesion",
        "lesion_subregion",
        "tissue_region",
    ]
    subregion: str | None = Field(default=None, max_length=120)
    laterality: Literal["left", "right", "bilateral", "unspecified"] = "unspecified"
    target_defining_modifiers: list[str] = Field(default_factory=list)

    accepted_mask_candidate_ids: list[str] = Field(default_factory=list)

    # The exact text sent to VoxTell. Stored separately for observability; an
    # invariant validator enforces equality with canonical_label so there is no
    # drift path in the current release.
    inference_prompt: str = Field(min_length=1, max_length=200)

    voxtell_prompt: VoxTellPromptDecision

    # Confidence fields are kept separate so debugging can distinguish a weak
    # clinical interpretation from a weak mask match.
    intent_confidence: float = Field(ge=0.0, le=1.0)
    match_confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=RESOLVED_REASON_MAX_LENGTH)

    @model_validator(mode="after")
    def validate_resolved_target(self):
        # inference_prompt must equal canonical_label (case-insensitive, whitespace-normalized).
        prompt = " ".join((self.inference_prompt or "").split()).casefold()
        canonical = " ".join((self.canonical_label or "").split()).casefold()
        if prompt != canonical:
            raise ValueError(
                "inference_prompt must equal canonical_label "
                f"(got {self.inference_prompt!r} vs {self.canonical_label!r})"
            )
        # Normalize to the canonical casing.
        self.inference_prompt = " ".join(self.canonical_label.split())

        # VoxTell generate-mode prompt must equal canonical_label.
        if self.voxtell_prompt.mode == "generate":
            vt_prompt = " ".join((self.voxtell_prompt.prompt or "").split()).casefold()
            if vt_prompt != canonical:
                raise ValueError(
                    "A generated VoxTell prompt must equal canonical_label"
                )
            self.voxtell_prompt.prompt = " ".join(self.canonical_label.split())

        # Composite reuse gate: both confidences must be above threshold.
        reuse_confidence = min(self.intent_confidence, self.match_confidence)
        if reuse_confidence < REUSE_CONFIDENCE_THRESHOLD:
            if self.accepted_mask_candidate_ids:
                raise ValueError(
                    "Low-confidence targets cannot reuse existing masks"
                )
            if self.voxtell_prompt.mode != "generate":
                raise ValueError(
                    "Low-confidence targets must generate the exact canonical prompt"
                )
        return self

    @property
    def confidence(self) -> float:
        """Composite confidence for backward-compatible code paths."""
        return min(self.intent_confidence, self.match_confidence)


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


class TargetResolutionOutcome(BaseModel):
    schema_version: int = CURRENT_SCHEMA_VERSION
    plan: TargetResolutionPlan
    intent_plan: ClinicalTargetIntentPlan
    candidate_plan: TargetCandidateDecisionPlan
    policy_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


# ---------------------------------------------------------------------------
# Code-derived concept list (never written by an agent)
# ---------------------------------------------------------------------------


def required_target_concepts(target: ClinicalTargetIntent) -> list[str]:
    """Return the minimal set of words that must appear in a valid VoxTell prompt.

    This list is derived mechanically from the structured fields so that the
    code — not the agent — guarantees concept presence. It is used for
    presence-only validation, never for clinical interpretation.
    """
    concepts: list[str] = [target.anatomy]
    if target.pathology:
        concepts.append(target.pathology)
    concepts.extend(target.target_defining_modifiers)
    if target.laterality != "unspecified":
        concepts.append(target.laterality)
    return [c for c in concepts if c]


# ---------------------------------------------------------------------------
# Deterministic reference validators (called by planner.py after agent output)
# ---------------------------------------------------------------------------


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
                    "exactly match its canonical label"
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
        canonical = intent_by_id[decision.target_id].canonical_label
        if decision.voxtell_prompt.mode == "cache":
            candidate_id = str(decision.voxtell_prompt.cache_candidate_id)
            if candidate_id not in cache_ids:
                raise ValueError(
                    f"Target {decision.target_id!r} returned unknown cache "
                    f"candidate ID: {candidate_id}"
                )
            cached_prompt = cache_by_id[candidate_id].prompt.strip()
            if cached_prompt.casefold() != canonical.casefold():
                raise ValueError(
                    f"VoxTell cache prompt for target {decision.target_id!r} "
                    "must exactly match its canonical label"
                )
        else:
            prompt = " ".join((decision.voxtell_prompt.prompt or "").split())
            if prompt.casefold() != canonical.casefold():
                raise ValueError(
                    "A generated VoxTell prompt must equal the locked canonical "
                    f"label for target {decision.target_id!r}"
                )
            decision.voxtell_prompt.prompt = canonical
    return plan
