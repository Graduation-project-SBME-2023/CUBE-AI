"""Clinical target resolution policy used by the segmentation agents."""

import hashlib

from ...clinical_roi_policy import ROI_IDENTITY_POLICY


CLINICAL_TARGET_POLICY = f"""# Clinical Target Resolution Policy

This policy guides agent judgment. It is not a synonym table and must never be
used by runtime code to match labels.

{ROI_IDENTITY_POLICY}

## Modifier test (applied to every qualifying word)

Ask: *Would removing this word change the voxel boundary, select a different
lesion, or collapse a separately delineated subregion into a broader ROI?*

- **Yes → target-defining modifier**: include in canonical_label and list in
  target_defining_modifiers.
- **No → contextual descriptor**: the word describes acquisition, treatment, or
  study context; include in contextual_descriptors only.

No modifier is universally target-defining or universally contextual. Judge
each one from the full clinical instruction and the locked ROI scope.

## Scope assignment

| Situation | scope |
|-----------|-------|
| Whole organ or tissue region, no pathology | whole_organ |
| Entire lesion / tumor / pathology (no subregion specified) | whole_lesion |
| A named compartment of a pathology (enhancing, necrotic, non-enhancing…) | lesion_subregion |
| A tissue-type region (e.g. white matter) | tissue_region |

## Clinical equivalence

- Use medical knowledge to interpret adjective/noun forms, inflection, word
  order, and separators.
- Equivalent wording is acceptable only when anatomy, pathology, scope,
  subregion, and laterality are all unchanged.
- Never reduce a pathological target to a whole-organ target.
- Never reduce a lesion subregion to the whole lesion.
- Never invent equivalence between different diseases, anatomical sites, or
  sides.
- Do not use a generic whole-body pathology candidate for an organ-specific
  target unless the instruction itself requests a generic target.

## Candidate reuse (Agent 2)

- Select an existing mask candidate only when it is clinically equivalent to
  the requested ROI: anatomy, pathology, scope, subregion, and laterality must
  all match.
- For Phase 1 (unstructured legacy candidates), evaluate clinical equivalence
  from the display_label text. If the label does not clearly preserve the
  complete target identity — including any target-defining modifier — reject
  the candidate.
- Select a VoxTell cache candidate only when its trimmed prompt **exactly
  matches** the canonical target label (case-insensitive). A longer descriptive
  phrase is not eligible even when it is clinically equivalent; generate the
  exact canonical prompt instead.
- Model name, algorithm, and manual/automatic method are provenance, not target
  identity.
- If equivalence is uncertain, select no mask/cache candidate and generate the
  exact canonical prompt.
- Candidate-equivalence confidence below {0.6} is uncertain and forbids reuse.

A cache miss is not a failure. Generate the exact canonical prompt rather than
widening the target to achieve a cache hit.

Before approving a plan, independently verify that no requested disease,
anatomy, target scope, subregion, or laterality was lost."""

CLINICAL_TARGET_POLICY_FINGERPRINT = hashlib.sha256(
    CLINICAL_TARGET_POLICY.encode("utf-8")
).hexdigest()
