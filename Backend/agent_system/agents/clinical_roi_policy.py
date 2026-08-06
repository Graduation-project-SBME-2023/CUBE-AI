"""Shared clinical ROI identity policy and internal validation contracts."""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ROI_IDENTITY_POLICY = """# Clinical ROI Identity Policy

Resolve the analysis region from what the study explicitly says will be
segmented or used for feature extraction. Apply these rules in order:

1. Explicit ROI wording has highest priority. Preserve its anatomy, pathology,
   laterality, and whole-organ versus pathological-tissue scope exactly as
   stated.

2. Features extracted from an organ (for example, "features extracted from the
   spleen") require the whole-organ ROI even when disease or cancer is the
   cohort label, class label, outcome, or clinical context. The canonical target
   is the organ name and pathology is null.

3. Features extracted from a lesion, tumor, nodule, or other named pathology
   require that pathological ROI. Do not add the containing whole organ unless
   it is also explicitly requested.

4. Return both whole-organ and pathological targets only when the study
   explicitly requests feature extraction or segmentation from both regions.

5. Never create, remove, or change an ROI merely because disease or cancer is
   mentioned. Healthy-versus-diseased organ classification does not imply lesion
   segmentation.

6. If a radiomics hypothesis names a disease and organ but does not say whether
   features come from the whole organ, a lesion/pathology, or both, the ROI is
   ambiguous and must be clarified rather than guessed.

7. Modifier test — use this test for every adjective or qualifying phrase:
   *Would removing this word change the voxel boundary, cause a different lesion
   to be selected, or collapse a separately delineated subregion into a broader
   ROI?* If yes, the modifier is target-defining and must be preserved in
   canonical_label and target_defining_modifiers. If no, the modifier describes
   acquisition, treatment, or context and belongs only in
   contextual_descriptors.
   No modifier is globally identity-defining or globally contextual — judge its
   role from the full clinical instruction and the locked ROI.

8. Express each canonical segmentation target as a short, anatomy-first noun
   label containing only target identity: target-defining laterality, anatomy,
   any target-defining modifier, and the base pathological entity when one is
   requested.

Examples:
- "Features extracted from the spleen classify healthy versus diseased spleens"
  → one whole-organ target: canonical_label="spleen", pathology=null,
    scope="whole_organ", modifiers=[].
- "Features extracted from the spleen classify healthy versus cancerous spleens"
  → one whole-organ target: canonical_label="spleen", pathology=null,
    scope="whole_organ", modifiers=[].
  (Cancer is the cohort label, not the segmentation target.)
- "Features extracted from splenic lesions classify benign versus malignant"
  → one pathological target: canonical_label="spleen lesion",
    scope="whole_lesion", modifiers=[].
- "Radiomic features from the enhancing tumor ROI in treated glioblastoma"
  → one subregion target: canonical_label="enhancing brain tumor",
    scope="lesion_subregion", subregion="enhancing component",
    modifiers=["enhancing"].
  (Removing "enhancing" would change the voxel boundary — it is target-defining.)
- "Segment the brain tumor on contrast-enhanced MRI"
  → one target: canonical_label="brain tumor", scope="whole_lesion",
    modifiers=[].
  ("Contrast-enhanced" describes the imaging acquisition, not the mask boundary;
   removing it does not change which voxels are included.)
- "Extract features from the non-enhancing tumor region"
  → one target: canonical_label="non-enhancing brain tumor",
    scope="lesion_subregion", subregion="non-enhancing component",
    modifiers=["non-enhancing"].
  (Removing "non-enhancing" would select a different or broader compartment.)
- "Features from the whole spleen and the splenic lesion"
  → two targets: canonical_label="spleen" and canonical_label="spleen lesion".
- "Use radiomics to detect splenic cancer"
  → ambiguous; ask which ROI supplies the features.
"""


_QUOTED_VOXTELL_COMMAND_PROMPT_RE = re.compile(
    r"(?P<prefix>\b(?:voxtell\s+)?prompt\s*(?:[:=]\s*)?)"
    r"(?P<quote>[\"'`])segment\s+(?P<label>[^\"'`\r\n]+?)(?P=quote)",
    flags=re.IGNORECASE,
)


def canonicalize_voxtell_prompt_instructions(
    instructions: str,
    canonical_labels: list[str] | tuple[str, ...] = (),
) -> str:
    """Replace command-style quoted VoxTell prompts with canonical labels."""
    canonical_by_label = {
        " ".join(label.split()).casefold(): " ".join(label.split())
        for label in canonical_labels
        if label and label.strip()
    }

    def replace(match: re.Match) -> str:
        label = " ".join(match.group("label").split())
        label_without_article = re.sub(
            r"^the\s+",
            "",
            label,
            count=1,
            flags=re.IGNORECASE,
        )
        article_free_canonical = canonical_by_label.get(
            label_without_article.casefold(),
            label_without_article,
        )
        canonical = canonical_by_label.get(label.casefold(), article_free_canonical)
        quote = match.group("quote")
        return f"{match.group('prefix')}{quote}{canonical}{quote}"

    return _QUOTED_VOXTELL_COMMAND_PROMPT_RE.sub(replace, instructions)


class RoiInterpretation(BaseModel):
    """Internal, non-persisted ROI decision locked during PICO validation."""

    scope: Literal["whole_organ", "whole_lesion", "lesion_subregion", "tissue_region", "both"]
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
