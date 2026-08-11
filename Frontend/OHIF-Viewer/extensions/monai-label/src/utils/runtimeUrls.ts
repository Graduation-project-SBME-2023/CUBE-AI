const normalizeBaseUrl = (value?: string) =>
  typeof value === 'string' && value.trim() ? value.replace(/\/+$/, '') : '';

export const getAgentsApiBaseUrl = () => {
  const runtimeConfig = (
    window as Window & {
      config?: { AGENTS_API?: string };
    }
  ).config;

  return (
    normalizeBaseUrl(runtimeConfig?.AGENTS_API) ||
    '/agents'
  );
};

export const getAppApiBaseUrl = () => {
  const runtimeConfig = (
    window as Window & {
      config?: { APP_API?: string; BACKEND_API?: string };
    }
  ).config;

  return normalizeBaseUrl(runtimeConfig?.APP_API || runtimeConfig?.BACKEND_API) || '/api';
};
