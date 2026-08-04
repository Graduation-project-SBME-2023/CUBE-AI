"""Clinical target resolution policy used by the segmentation agents."""

import hashlib

from ...clinical_roi_policy import ROI_IDENTITY_POLICY


CLINICAL_TARGET_POLICY = f"""# Clinical Target Resolution Policy

This policy guides agent judgment. It is not a synonym table and must never be
used by runtime code to match labels.

{ROI_IDENTITY_POLICY}

## Clinical equivalence

- Use medical knowledge to interpret adjective/noun forms, inflection, word
  order, and separators.
- Equivalent wording is acceptable only when anatomy, pathology, target scope,
  and laterality are unchanged.
- Never reduce a pathological target to a whole-organ target.
- Never invent equivalence between different diseases, anatomical sites, or
  sides.
- Do not use a generic whole-body pathology candidate for an organ-specific
  target unless the instruction itself requests a generic target.

## Candidate reuse

- Select an existing mask candidate only when it is clinically equivalent to
  the requested ROI.
- Select a VoxTell cache candidate only when its trimmed prompt exactly matches
  the compact canonical target label (case-insensitive). A longer descriptive
  phrase is not eligible even when it is clinically equivalent; generate the
  exact canonical prompt instead.
- Model name, algorithm, and manual/automatic method are provenance, not target
  identity.
- If equivalence is uncertain, select no mask/cache candidate and generate the
  exact canonical prompt.
- Candidate-equivalence confidence below 0.6 is uncertain and forbids reuse.

Before approving a plan, independently verify that no requested disease,
anatomy, target scope, or laterality was lost."""

CLINICAL_TARGET_POLICY_FINGERPRINT = hashlib.sha256(
    CLINICAL_TARGET_POLICY.encode("utf-8")
).hexdigest()
