const normalizeClinicalLabel = (value) =>
  String(value || '')
    .trim()
    .replace(/_/g, ' ')
    .replace(/\s+/g, ' ');

/**
 * Resolve the labels attached to an inference result when it is exported.
 *
 * VoxTell returns a binary mask, so foreground index 1 represents the text
 * prompt used for this inference.  The server-wide label catalog must not be
 * used here: its first entry can be an unrelated organ (historically spleen).
 * Fixed multi-label models still use their declared server labels.
 */
export default function resolveInferenceSegmentLabels(
  model,
  targetLabel,
  serverLabels
) {
  const labels = Array.isArray(serverLabels) ? serverLabels : [];

  if (
    String(model || '')
      .trim()
      .toLowerCase() !== 'voxtell'
  ) {
    return labels;
  }

  const normalizedTarget = normalizeClinicalLabel(targetLabel);
  if (!normalizedTarget) {
    throw new Error(
      'VoxTell target label is required to export a segmentation'
    );
  }

  return [normalizedTarget];
}

function synchronizeInferenceSegmentLabels(
  segmentationService,
  segmentationId,
  labels
) {
  const segmentation = segmentationService.getSegmentation(segmentationId);
  if (!segmentation) {
    throw new Error(`No segmentation found for ID: ${segmentationId}`);
  }

  labels.forEach((label, index) => {
    const segmentIndex = index + 1;
    if (!segmentation.segments?.[segmentIndex]) {
      throw new Error(
        `Segment ${segmentIndex} is unavailable for segmentation ${segmentationId}`
      );
    }
    segmentationService.setSegmentLabel(segmentationId, segmentIndex, label);
  });
}

export { normalizeClinicalLabel, synchronizeInferenceSegmentLabels };
