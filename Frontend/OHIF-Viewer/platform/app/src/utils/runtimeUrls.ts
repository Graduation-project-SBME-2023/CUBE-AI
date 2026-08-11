const getEnv = () => (typeof process !== 'undefined' ? process.env : undefined);

const normalizeBaseUrl = (value?: string) => {
  if (typeof value === 'string' && value.trim()) {
    return value.replace(/\/+$/, '');
  }

  return '';
};

const sameOriginWebSocketUrl = (path: string) =>
  `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}${path}`;

export const getAppApiBaseUrl = () =>
  normalizeBaseUrl(getEnv()?.REACT_APP_APP_API_URL) ||
  normalizeBaseUrl(getEnv()?.REACT_APP_BACKEND_API) ||
  '/api';

export const getInferenceBaseUrl = () =>
  normalizeBaseUrl(getEnv()?.REACT_APP_INFERENCE_URL) ||
  normalizeBaseUrl(getEnv()?.REACT_APP_MONAI_URL) ||
  '/inference';

// Compatibility name for MONAI-specific components only.
export const getMonaiBaseUrl = getInferenceBaseUrl;

export const getAgentsBaseUrl = () =>
  normalizeBaseUrl(getEnv()?.REACT_APP_AGENTS_URL) || '/agents';

export const getAgentsWsUrl = () => {
  const envUrl = normalizeBaseUrl(getEnv()?.REACT_APP_AGENTS_WS_URL);
  if (envUrl) {
    return envUrl;
  }

  return sameOriginWebSocketUrl('/ws');
};
