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
    `${window.location.protocol}//${window.location.hostname}:8150`
  );
};
