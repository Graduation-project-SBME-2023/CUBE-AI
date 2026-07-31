"""Clinical target resolution policy used by the segmentation agents."""

import hashlib


CLINICAL_TARGET_POLICY = """# Clinical Target Resolution Policy

This policy guides agent judgment. It is not a synonym table and must never be
used by runtime code to match labels.

## Resolve the requested ROI

1. Identify the ROI actually requested by the instruction or hypothesis.
2. Decide whether it is a whole anatomical structure or pathological tissue.
3. Preserve anatomy, pathology, and laterality in the canonical label.
4. Treat disease mentioned only as cohort, outcome, or background context
   separately from disease that defines the ROI.
5. Honor explicit whole-organ language such as "whole", "entire", or
   "organ mask", even when the organ belongs to a diseased patient.

## Clinical equivalence

- Use medical knowledge to interpret adjective/noun forms, inflection, word
  order, and separators.
- Equivalent wording is acceptable only when anatomy, pathology, target scope,
  and laterality are unchanged.
- Never reduce a pathological target to a healthy-organ target.
- Never invent equivalence between different diseases, anatomical sites, or
  sides.
- Do not use a generic whole-body pathology candidate for an organ-specific
  target unless the instruction itself requests a generic target.

## Candidate reuse

- Select a mask or VoxTell cache candidate only when it is clinically
  equivalent to the requested ROI.
- Model name, algorithm, and manual/automatic method are provenance, not target
  identity.
- If equivalence is uncertain, select no mask/cache candidate and generate the
  exact canonical prompt.
- Candidate-equivalence confidence below 0.6 is uncertain and forbids reuse.

## Decision examples

- "Segment the spleen." → whole-organ target `spleen`, pathology `null`.
- "Segment the spleen in patients with cancer." → whole-organ target `spleen`;
  cancer describes the cohort, not the ROI.
- "Segment the whole cancerous spleen." → whole-organ target `spleen` because
  the requested boundary is explicit.
- "Radiomics can classify cancerous spleens." → pathological spleen target,
  such as `spleen lesion`; do not resolve to healthy `spleen`.
- "Segment splenic lesions." → pathological target `spleen lesion`.
- "Segment the left renal lesion." → pathological target
  `left kidney lesion`; preserve left laterality.

Before approving a plan, independently verify that no requested disease,
anatomy, target scope, or laterality was lost."""

CLINICAL_TARGET_POLICY_FINGERPRINT = hashlib.sha256(
    CLINICAL_TARGET_POLICY.encode("utf-8")
).hexdigest()
