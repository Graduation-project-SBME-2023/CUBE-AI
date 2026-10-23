---
name: segmentation_choices
description: Locking the region of interest and briefing the segmentation step - ROI identity, target labels, mask reuse and review.
---
# Segmentation choices

The segmentation step segments the frozen cohort: it reuses existing masks
that are clinically equivalent to the target, generates the rest with a
text-prompted model (VoxTell), and, when the user asked for a review, waits
for the radiologist's. The
clinical decisions inside it follow the same policy as below, so the region
of interest you lock here is the one it segments. `list_models` shows the
models available; `check_having_mask` shows whether a volume already has one.

## Briefing the step

- State the locked region of interest explicitly: the organ, the lesion, or
  both, exactly as the study defines it.
- Write each target as a short, anatomy-first noun label (`spleen`,
  `enhancing brain tumor`), never as a command (`segment spleen`).
- Keep every target-defining modifier (`enhancing`, `left`), and leave
  acquisition or context words (`contrast-enhanced`, `treated`) out of the
  label.
- Do not add a target the study did not ask for: a healthy-versus-diseased
  organ study segments the organ, not its lesions.

When the user asked to review the masks, the radiologist reviews every mask
before any later step uses it, and the node waits for that review, holding
no resources; otherwise the masks are accepted automatically. When the user
asks why the study is not moving, a pending review is often the reason.

## The region of interest identity policy

This is the policy the planners have always applied; it is copied here word
for word, and a test keeps it identical to its source.

# Clinical ROI Identity Policy

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
