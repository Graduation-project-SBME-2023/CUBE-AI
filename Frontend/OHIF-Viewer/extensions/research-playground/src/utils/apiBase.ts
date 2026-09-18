import config from '../config';

const getFallbackBaseUrl = (port: number) => `${window.location.protocol}//${window.location.hostname}:${port}`;

export const getApiBaseUrl = (baseUrl: string | undefined | null, fallbackPort: number) => {
  if (typeof baseUrl === 'string' && baseUrl.trim()) {
    return baseUrl.replace(/\/+$/, '');
  }

  return getFallbackBaseUrl(fallbackPort);
};

export const getBackendApiBaseUrl = () => getApiBaseUrl(config?.BACKEND_API, 8001);

export const getInferenceApiBaseUrl = () => getApiBaseUrl(config?.INFERENCE_URL || config?.MONAI_URL, 8000);

export const getAgentsApiBaseUrl = () => getApiBaseUrl(config?.AGENTS_API, 8150);

export const getPacsApiBaseUrl = () => getApiBaseUrl(config?.PACS_URL, 8043);
