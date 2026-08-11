const getEnv = () => (typeof process !== 'undefined' ? process.env : undefined);

const normalizeBaseUrl = (value?: string) => {
  if (typeof value === 'string' && value.trim()) {
    return value.replace(/\/+$/, '');
  }

  return '';
};

const sameOriginWebSocketUrl = (path: string) =>
  `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}${path}`;

const usesDirectServicePorts = () =>
  getEnv()?.NODE_ENV === 'development' || ['3000', '3001'].includes(window.location.port);

const directHttpUrl = (port: number) =>
  `${window.location.protocol}//${window.location.hostname}:${port}`;

const directAgentsWebSocketUrl = () =>
  `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.hostname}:8150/ws`;

export const getAppApiBaseUrl = () =>
  normalizeBaseUrl(getEnv()?.REACT_APP_APP_API_URL) ||
  normalizeBaseUrl(getEnv()?.REACT_APP_BACKEND_API) ||
  (usesDirectServicePorts() ? directHttpUrl(8001) : '/api');

export const getInferenceBaseUrl = () =>
  normalizeBaseUrl(getEnv()?.REACT_APP_INFERENCE_URL) ||
  normalizeBaseUrl(getEnv()?.REACT_APP_MONAI_URL) ||
  (usesDirectServicePorts() ? directHttpUrl(8000) : '/inference');

// Compatibility name for MONAI-specific components only.
export const getMonaiBaseUrl = getInferenceBaseUrl;

export const getAgentsBaseUrl = () =>
  normalizeBaseUrl(getEnv()?.REACT_APP_AGENTS_URL) ||
  (usesDirectServicePorts() ? directHttpUrl(8150) : '/agents');

export const getAgentsWsUrl = () => {
  const envUrl = normalizeBaseUrl(getEnv()?.REACT_APP_AGENTS_WS_URL);
  if (envUrl) {
    return envUrl;
  }

  return usesDirectServicePorts() ? directAgentsWebSocketUrl() : sameOriginWebSocketUrl('/ws');
};
