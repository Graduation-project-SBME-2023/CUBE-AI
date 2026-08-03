import { Enums, cache } from '@cornerstonejs/core';
import { utilities as csToolsUtils } from '@cornerstonejs/tools';

function getDisplaySetFrameOfReferenceUID(displaySet) {
  const referencedFrameOfReferenceSequence = Array.isArray(
    displaySet?.instance?.ReferencedFrameOfReferenceSequence
  )
    ? displaySet.instance.ReferencedFrameOfReferenceSequence[0]
    : displaySet?.instance?.ReferencedFrameOfReferenceSequence;

  return (
    displaySet?.frameOfReferenceUID ||
    displaySet?.FrameOfReferenceUID ||
    displaySet?.instances?.[0]?.FrameOfReferenceUID ||
    displaySet?.images?.[0]?.FrameOfReferenceUID ||
    displaySet?.instance?.FrameOfReferenceUID ||
    referencedFrameOfReferenceSequence?.FrameOfReferenceUID
  );
}

/**
 * Updates the viewports in preparation for rendering segmentations.
 * Evaluates each viewport to determine which need modifications,
 * then for those viewports, changes them to a volume type and ensures
 * they are ready for segmentation rendering.
 *
 * @param {Object} params - Parameters for the function.
 * @param params.viewportId - ID of the viewport to be updated.
 * @param params.loadFn - Function to load the segmentation data.
 * @param params.servicesManager - The services manager.
 * @param params.referencedDisplaySetInstanceUID - Optional UID for the referenced display set instance.
 *
 * @returns Returns true upon successful update of viewports for segmentation rendering.
 */
async function updateViewportsForSegmentationRendering({
  viewportId,
  loadFn,
  servicesManager,
  referencedDisplaySetInstanceUID,
}: {
  viewportId: string;
  loadFn: () => Promise<string>;
  servicesManager: any;
  referencedDisplaySetInstanceUID?: string;
}) {
  const {
    cornerstoneViewportService,
    displaySetService,
    segmentationService,
    toolGroupService,
    viewportGridService,
  } = servicesManager.services;

  const viewport = getTargetViewport({ viewportId, viewportGridService });
  const targetViewportId = viewport.viewportOptions.viewportId;

  referencedDisplaySetInstanceUID =
    referencedDisplaySetInstanceUID || viewport?.displaySetInstanceUIDs[0];

  const updatedViewports = getUpdatedViewportsForSegmentation({
    servicesManager,
    viewportId,
    referencedDisplaySetInstanceUID,
  });

  // create Segmentation callback which needs to be waited until
  // the volume is created (if coming from stack)
  let segmentationPromise;

  const createSegmentationForVolume = async () => {
    const segmentationId = await loadFn();
    const segmentation = segmentationService.getSegmentation(segmentationId);
    const referencedDisplaySet = displaySetService.getDisplaySetByUID(
      referencedDisplaySetInstanceUID
    );
    const referencedFrameOfReferenceUID = getDisplaySetFrameOfReferenceUID(referencedDisplaySet);

    // A DICOM SEG belongs to the referenced image series. Prefer that series'
    // Frame of Reference over incomplete or inconsistent SEG metadata so the
    // representation can be associated with the active viewport reliably.
    if (segmentation && referencedFrameOfReferenceUID) {
      segmentation.FrameOfReferenceUID = referencedFrameOfReferenceUID;
    }

    segmentationService.hydrateSegmentation(segmentationId);

    const toolGroup = toolGroupService.getToolGroupForViewport(targetViewportId);
    if (toolGroup) {
      const representations =
        segmentationService.getSegmentationRepresentationsForToolGroup(toolGroup.id) || [];
      const representationExists = representations.some(
        representation => representation.segmentationId === segmentationId
      );

      if (!representationExists) {
        await segmentationService.addSegmentationRepresentationToToolGroup(
          toolGroup.id,
          segmentationId,
          false,
          segmentation?.type
        );
      }

      csToolsUtils.segmentation.triggerSegmentationRender(toolGroup.id);
    }

    cornerstoneViewportService.getCornerstoneViewport(targetViewportId)?.render();

    return segmentationId;
  };

  const createSegmentationOnce = () => {
    segmentationPromise ||= createSegmentationForVolume();
    return segmentationPromise;
  };

  // the reference volume that is used to draw the segmentation. so check if the
  // volume exists in the cache (the target Viewport is already a volume viewport)
  const volumeExists = Array.from(cache._volumeCache.keys()).some(volumeId =>
    volumeId.includes(referencedDisplaySetInstanceUID)
  );

  let targetViewportHydrationPromise;

  updatedViewports.forEach(viewport => {
    viewport.viewportOptions = {
      ...viewport.viewportOptions,
      viewportType: 'volume',
      needsRerendering: true,
    };
    const viewportId = viewport.viewportId;

    const csViewport = cornerstoneViewportService.getCornerstoneViewport(viewportId);
    const prevCamera = csViewport.getCamera();

    // only run the createSegmentationForVolume for the targetViewportId
    // since the rest will get handled by cornerstoneViewportService
    if (volumeExists && viewportId === targetViewportId) {
      targetViewportHydrationPromise = createSegmentationOnce();
      return;
    }

    let resolveTargetViewportHydration;
    let rejectTargetViewportHydration;

    if (viewportId === targetViewportId) {
      targetViewportHydrationPromise = new Promise((resolve, reject) => {
        resolveTargetViewportHydration = resolve;
        rejectTargetViewportHydration = reject;
      });
    }

    const createNewSegmentationWhenVolumeMounts = async evt => {
      const isTheActiveViewportVolumeMounted = evt.detail.volumeActors?.find(ac =>
        ac.uid.includes(referencedDisplaySetInstanceUID)
      );

      if (!isTheActiveViewportVolumeMounted) {
        return;
      }

      // Note: make sure to re-grab the viewport since it might have changed
      // during the time it took for the volume to be mounted, for instance
      // the stack viewport has been changed to a volume viewport
      const volumeViewport = cornerstoneViewportService.getCornerstoneViewport(viewportId);
      volumeViewport.setCamera(prevCamera);

      volumeViewport.element.removeEventListener(
        Enums.Events.VOLUME_VIEWPORT_NEW_VOLUME,
        createNewSegmentationWhenVolumeMounts
      );

      if (viewportId === targetViewportId) {
        try {
          await createSegmentationOnce();
          resolveTargetViewportHydration?.(true);
        } catch (error) {
          rejectTargetViewportHydration?.(error);
        }
      }
    };

    csViewport.element.addEventListener(
      Enums.Events.VOLUME_VIEWPORT_NEW_VOLUME,
      createNewSegmentationWhenVolumeMounts
    );
  });

  // Set the displaySets for the viewports that require to be updated
  viewportGridService.setDisplaySetsForViewports(updatedViewports);

  await targetViewportHydrationPromise;

  return true;
}

const getTargetViewport = ({ viewportId, viewportGridService }) => {
  const { viewports, activeViewportId } = viewportGridService.getState();
  const targetViewportId = viewportId || activeViewportId;

  const viewport = viewports.get(targetViewportId);

  return viewport;
};

/**
 * Retrieves a list of viewports that require updates in preparation for segmentation rendering.
 * This function evaluates viewports based on their compatibility with the provided segmentation's
 * frame of reference UID and appends them to the updated list if they should render the segmentation.
 *
 * @param {Object} params - Parameters for the function.
 * @param params.viewportId - the ID of the viewport to be updated.
 * @param params.servicesManager - The services manager
 * @param params.referencedDisplaySetInstanceUID - Optional UID for the referenced display set instance.
 *
 * @returns {Array} Returns an array of viewports that require updates for segmentation rendering.
 */
function getUpdatedViewportsForSegmentation({
  viewportId,
  servicesManager,
  referencedDisplaySetInstanceUID,
}) {
  const { hangingProtocolService, displaySetService, segmentationService, viewportGridService } =
    servicesManager.services;

  const { viewports } = viewportGridService.getState();

  const viewport = getTargetViewport({ viewportId, viewportGridService });
  const targetViewportId = viewport.viewportOptions.viewportId;

  const displaySetInstanceUIDs = viewports.get(targetViewportId).displaySetInstanceUIDs;

  const referenceDisplaySetInstanceUID =
    referencedDisplaySetInstanceUID || displaySetInstanceUIDs[0];

  const referencedDisplaySet = displaySetService.getDisplaySetByUID(referenceDisplaySetInstanceUID);
  const segmentationFrameOfReferenceUID = getDisplaySetFrameOfReferenceUID(referencedDisplaySet);

  if (!segmentationFrameOfReferenceUID) {
    return hangingProtocolService.getViewportsRequireUpdate(
      targetViewportId,
      referenceDisplaySetInstanceUID
    );
  }

  const updatedViewports = hangingProtocolService.getViewportsRequireUpdate(
    targetViewportId,
    referenceDisplaySetInstanceUID
  );

  viewports.forEach((viewport, viewportId) => {
    if (
      targetViewportId === viewportId ||
      updatedViewports.find(v => v.viewportId === viewportId)
    ) {
      return;
    }

    const shouldDisplaySeg = segmentationService.shouldRenderSegmentation(
      viewport.displaySetInstanceUIDs,
      segmentationFrameOfReferenceUID
    );

    if (shouldDisplaySeg) {
      updatedViewports.push({
        viewportId,
        displaySetInstanceUIDs: viewport.displaySetInstanceUIDs,
        viewportOptions: {
          viewportType: 'volume',
          needsRerendering: true,
        },
      });
    }
  });
  return updatedViewports;
}

export {
  updateViewportsForSegmentationRendering,
  getUpdatedViewportsForSegmentation,
  getTargetViewport,
};
