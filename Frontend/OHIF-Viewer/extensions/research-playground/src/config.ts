// Centralized runtime endpoints — prefer environment variables
const processEnv = typeof process !== 'undefined' ? process.env : undefined;
const defaultAppApiUrl = '/api';
const defaultInferenceUrl = '/inference';
const defaultAgentsBaseUrl = '/agents';
const defaultAgentsWsUrl = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`;

export const BACKEND_API =
  processEnv?.REACT_APP_APP_API_URL ||
  processEnv?.REACT_APP_BACKEND_API ||
  defaultAppApiUrl;

export const AGENTS_API =
  processEnv?.REACT_APP_AGENTS_API ||
  (processEnv?.REACT_APP_AGENTS_PORT
    ? `${window.location.protocol}//${window.location.hostname}:${processEnv.REACT_APP_AGENTS_PORT}`
    : defaultAgentsBaseUrl);

export const AGENTS_WS =
  processEnv?.REACT_APP_AGENTS_WS ||
  (processEnv?.REACT_APP_AGENTS_WS_PORT
    ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.hostname}:${processEnv.REACT_APP_AGENTS_WS_PORT}/ws`
    : defaultAgentsWsUrl);

export const INFERENCE_URL =
  processEnv?.REACT_APP_INFERENCE_URL ||
  processEnv?.REACT_APP_MONAI_URL ||
  defaultInferenceUrl;
export const MONAI_URL = INFERENCE_URL;

export default {
  BACKEND_API,
  AGENTS_API,
  AGENTS_WS,
  INFERENCE_URL,
  MONAI_URL,
};
