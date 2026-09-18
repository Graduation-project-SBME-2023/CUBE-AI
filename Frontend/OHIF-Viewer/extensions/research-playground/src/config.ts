// Centralized runtime endpoints — prefer environment variables
const processEnv = typeof process !== 'undefined' ? process.env : undefined;
const usesDirectServicePorts =
  processEnv?.NODE_ENV === 'development' || ['3000', '3001'].includes(window.location.port);
const defaultAppApiUrl = usesDirectServicePorts
  ? `${window.location.protocol}//${window.location.hostname}:8001`
  : '/api';
const defaultInferenceUrl = usesDirectServicePorts
  ? `${window.location.protocol}//${window.location.hostname}:8000`
  : '/inference';
const defaultAgentsBaseUrl = usesDirectServicePorts
  ? `${window.location.protocol}//${window.location.hostname}:8150`
  : '/agents';
const defaultAgentsWsUrl = usesDirectServicePorts
  ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.hostname}:8150/ws`
  : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`;
// Same-origin in every environment, unlike the services above. The other
// backends publish CORS_ORIGINS and so can be called on their own port from
// the dev server, but Orthanc sends no CORS headers at all -- a POST straight
// to :8043 from http://localhost:3000 fails the preflight and surfaces only as
// "Failed to fetch", with nothing in Orthanc's log to say a request arrived.
// Both fronts already proxy this prefix: webpack's devServer maps /pacs to
// :8043, and OpenResty maps it to the orthanc container.
const defaultPacsUrl = '/pacs';

export const BACKEND_API =
  processEnv?.REACT_APP_APP_API_URL ||
  processEnv?.REACT_APP_BACKEND_API ||
  defaultAppApiUrl;

export const AGENTS_API =
  processEnv?.REACT_APP_AGENTS_URL ||
  processEnv?.REACT_APP_AGENTS_API ||
  (processEnv?.REACT_APP_AGENTS_PORT
    ? `${window.location.protocol}//${window.location.hostname}:${processEnv.REACT_APP_AGENTS_PORT}`
    : defaultAgentsBaseUrl);

export const AGENTS_WS =
  processEnv?.REACT_APP_AGENTS_WS_URL ||
  processEnv?.REACT_APP_AGENTS_WS ||
  (processEnv?.REACT_APP_AGENTS_WS_PORT
    ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.hostname}:${processEnv.REACT_APP_AGENTS_WS_PORT}/ws`
    : defaultAgentsWsUrl);

export const INFERENCE_URL =
  processEnv?.REACT_APP_INFERENCE_URL ||
  processEnv?.REACT_APP_MONAI_URL ||
  defaultInferenceUrl;
export const MONAI_URL = INFERENCE_URL;

// Orthanc's own REST root, used to store DICOM directly. DICOMweb reads still
// go through the data-source configuration, not this.
export const PACS_URL = processEnv?.REACT_APP_PACS_URL || defaultPacsUrl;

export default {
  BACKEND_API,
  AGENTS_API,
  AGENTS_WS,
  INFERENCE_URL,
  MONAI_URL,
  PACS_URL,
};
