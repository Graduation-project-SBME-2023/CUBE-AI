const getEnv = () => (typeof process !== 'undefined' ? process.env : undefined);

const normalizeBaseUrl = (value?: string) => {
  if (typeof value === 'string' && value.trim()) {
    return value.replace(/\/+$/, '');
  }

  return '';
};

const fallbackBaseUrl = (port: number) => `${window.location.protocol}//${window.location.hostname}:${port}`;

export const getMonaiBaseUrl = () =>
  normalizeBaseUrl(getEnv()?.REACT_APP_MONAI_URL) || fallbackBaseUrl(8000);

export const getAgentsBaseUrl = () =>
  normalizeBaseUrl(getEnv()?.REACT_APP_AGENTS_URL) || fallbackBaseUrl(8150);

export const getAgentsWsUrl = () => {
  const envUrl = normalizeBaseUrl(getEnv()?.REACT_APP_AGENTS_WS_URL);
  if (envUrl) {
    return envUrl;
  }

  return `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.hostname}:8150/ws`;
};
