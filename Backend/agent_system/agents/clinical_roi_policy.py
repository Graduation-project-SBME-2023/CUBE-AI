"""Shared clinical ROI identity policy and internal validation contracts."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ROI_IDENTITY_POLICY = """# Clinical ROI Identity Policy

Resolve the analysis region from what the study explicitly says will be
segmented or used for feature extraction. Apply these rules in order:

1. Explicit ROI wording has highest priority. Preserve its anatomy, pathology,
   laterality, and whole-organ versus pathological-tissue scope.
2. Features extracted from an organ (for example, "features extracted from the
   spleen") require the whole-organ ROI even when disease or cancer is the
   cohort, class label, outcome, or clinical context. The canonical target is
   the organ name and pathology is null.
3. Features extracted from a lesion, tumor, nodule, or other named pathology
   require that pathological ROI. Do not add the containing whole organ unless
   it is also explicitly requested.
4. Return both whole-organ and pathological targets only when the study
   explicitly requests feature extraction or segmentation from both regions.
5. Never create, remove, or change an ROI merely because disease or cancer is
   mentioned. In particular, healthy-versus-diseased organ classification does
   not imply lesion segmentation.
6. If a radiomics hypothesis names a disease and organ but does not say whether
   features come from the whole organ, a lesion/pathology, or both, the ROI is
   ambiguous and must be clarified rather than guessed.
7. Express each canonical segmentation target as a short, anatomy-first noun
   label containing only target identity: target-defining laterality, anatomy,
   and the base pathological entity when one is requested. Imaging appearance,
   acquisition, treatment, size, and other descriptive context belong in the
   rationale, not in the target label or VoxTell prompt. For example,
   "enhancing tumor of brain" has the canonical target `brain tumor`, with
   anatomy `brain` and pathology `tumor`.

Examples:
- "Features extracted from the spleen classify healthy versus diseased
  spleens" -> one whole-organ target: `spleen`, pathology `null`.
- "Features extracted from the spleen classify healthy versus cancerous
  spleens" -> one whole-organ target: `spleen`, pathology `null`.
- "Features extracted from splenic lesions classify benign versus malignant
  lesions" -> one pathological target: `spleen lesion`.
- "Features from the whole spleen and the splenic lesion" -> two targets:
  `spleen` and `spleen lesion`.
- "Features from the enhancing tumor of brain" -> one pathological target:
  `brain tumor`; `enhancing` is imaging description, not target identity.
- "Use radiomics to detect splenic cancer" -> ambiguous; ask which ROI supplies
  the features.
"""


class RoiInterpretation(BaseModel):
    """Internal, non-persisted ROI decision locked during PICO validation."""

    scope: Literal["whole_organ", "pathology", "both"]
    labels: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def normalize_and_validate(self):
        self.labels = [label.strip() for label in self.labels]
        self.rationale = self.rationale.strip()
        if any(not label for label in self.labels) or not self.rationale:
            raise ValueError("ROI labels and rationale cannot be blank")
        normalized = [label.casefold() for label in self.labels]
        if len(normalized) != len(set(normalized)):
            raise ValueError("ROI labels must be unique")
        if self.scope == "both" and len(self.labels) < 2:
            raise ValueError("ROI scope 'both' requires at least two labels")
        return self


class HypothesisValidationResult(BaseModel):
    """Validated internal response from the PICO/ROI validation stage."""

    valid: bool
    feedback: str = ""
    extracted: dict[str, Any] | None = None
    roi_interpretation: RoiInterpretation | None = None

    @model_validator(mode="after")
    def require_complete_valid_result(self):
        self.feedback = self.feedback.strip()
        if self.valid:
            if self.extracted is None:
                raise ValueError("A valid hypothesis requires extracted PICO data")
            if self.roi_interpretation is None:
                raise ValueError("A valid hypothesis requires a locked ROI interpretation")
        elif not self.feedback:
            raise ValueError("An invalid hypothesis requires clarification feedback")
        return self
