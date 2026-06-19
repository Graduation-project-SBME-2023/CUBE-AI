// Centralized runtime endpoints — prefer environment variables
const processEnv = typeof process !== 'undefined' ? process.env : undefined;
const defaultMonaiBaseUrl = `${window.location.protocol}//${window.location.hostname}:8000`;
const defaultAgentsBaseUrl = `${window.location.protocol}//${window.location.hostname}:8150`;
const defaultAgentsWsUrl = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.hostname}:8150/ws`;

export const BACKEND_API =
  processEnv?.REACT_APP_BACKEND_API ||
  defaultMonaiBaseUrl;

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

export const MONAI_URL = processEnv?.REACT_APP_MONAI_URL || BACKEND_API;

export default {
  BACKEND_API,
  AGENTS_API,
  AGENTS_WS,
  MONAI_URL,
};
