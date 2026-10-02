import { getAppApiBaseUrl } from './runtimeUrls';

/**
 * Tell the Application API that this mask came from the assisted path.
 *
 * The Orthanc sync cannot work this out on its own: the assisted path and the
 * pipeline path emit an identical `dd/mm/yyyy - model - label`
 * SeriesDescription, so the sync records `origin: 'imported'` — which is not
 * reusable inventory — and the producer declares the truth. An assisted mask
 * is STOW'd with no review gate, so declaring it here is what makes it
 * reusable.
 *
 * Failure is logged, never thrown: the SEG is already in Orthanc and the row
 * is still correct, only less specific. Losing the user's mask because a
 * metadata call failed would be the worse outcome.
 */
export async function declareAssistedSegmentationOrigin(
  naturalizedReport: Record<string, any>,
  options: { modelName?: string; seriesDescription?: string } = {}
): Promise<void> {
  const labelSeriesInstanceUID = naturalizedReport?.SeriesInstanceUID;
  const referencedSeries = naturalizedReport?.ReferencedSeriesSequence;
  const referenced = Array.isArray(referencedSeries) ? referencedSeries[0] : referencedSeries;
  const volumeSeriesInstanceUID = referenced?.SeriesInstanceUID;

  if (!labelSeriesInstanceUID || !volumeSeriesInstanceUID) {
    console.warn(
      'Skipping segmentation origin declaration: missing label or source series UID'
    );
    return;
  }

  try {
    const response = await fetch(`${getAppApiBaseUrl()}/segmentation-masks/declare-origin`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        label_series_instance_uid: labelSeriesInstanceUID,
        volume_series_instance_uid: volumeSeriesInstanceUID,
        origin: 'assisted',
        is_empty_mask: false,
        generation_method: 'mask_editor',
        series_description: options.seriesDescription,
        model_name: options.modelName,
      }),
    });
    if (!response.ok) {
      console.warn(
        `Segmentation origin declaration rejected: ${response.status} ${response.statusText}`
      );
    }
  } catch (error) {
    console.warn('Segmentation origin declaration failed', error);
  }
}
