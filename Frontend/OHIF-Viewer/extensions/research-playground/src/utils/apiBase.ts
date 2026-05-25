import config from '../config';

const getFallbackBaseUrl = (port: number) => `${window.location.protocol}//${window.location.hostname}:${port}`;

export const getApiBaseUrl = (baseUrl: string | undefined | null, fallbackPort: number) => {
  if (typeof baseUrl === 'string' && baseUrl.trim()) {
    return baseUrl.replace(/\/+$/, '');
  }

  return getFallbackBaseUrl(fallbackPort);
};

export const getBackendApiBaseUrl = () => getApiBaseUrl(config?.MONAI_URL || config?.BACKEND_API, 8000);

export const getAgentsApiBaseUrl = () => getApiBaseUrl(config?.AGENTS_API, 8150);
