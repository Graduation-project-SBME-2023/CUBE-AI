// Same resolution rule as extensions/monai-label/src/utils/runtimeUrls.ts and
// platform/app/src/utils/runtimeUrls.ts: never hardcode a service URL, and
// auto-switch between the direct dev ports and the same-origin production
// prefixes that OpenResty strips.
const normalizeBaseUrl = (value?: string) =>
  typeof value === 'string' && value.trim() ? value.replace(/\/+$/, '') : '';

const usesDirectServicePorts = () =>
  (typeof process !== 'undefined' && process.env?.NODE_ENV === 'development') ||
  ['3000', '3001'].includes(window.location.port);

const directHttpUrl = (port: number) =>
  `${window.location.protocol}//${window.location.hostname}:${port}`;

export const getAppApiBaseUrl = () => {
  const runtimeConfig = (
    window as Window & {
      config?: { APP_API?: string; BACKEND_API?: string };
    }
  ).config;

  return (
    normalizeBaseUrl(runtimeConfig?.APP_API || runtimeConfig?.BACKEND_API) ||
    (usesDirectServicePorts() ? directHttpUrl(8001) : '/api')
  );
};
