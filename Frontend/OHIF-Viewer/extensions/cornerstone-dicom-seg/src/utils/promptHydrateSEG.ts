import { ButtonEnums } from '@ohif/ui';

const RESPONSE = {
  NO_NEVER: -1,
  CANCEL: 0,
  HYDRATE_SEG: 5,
};

function promptHydrateSEG({
  servicesManager,
  segDisplaySet,
  viewportId,
  preHydrateCallbacks,
  hydrateSEGDisplaySet,
}) {
  return new Promise(async function (resolve, reject) {
    // Auto-hydrate without showing a dialog
    preHydrateCallbacks?.forEach(callback => {
      callback();
    });

    const isHydrated = await hydrateSEGDisplaySet({
      segDisplaySet,
      viewportId,
    });

    resolve(isHydrated);
  });
}

export default promptHydrateSEG;

