// Single shared cache for GET /client-config, fetched by the agents backend
// once per app session instead of once per mounted consumer
// (HomeDashboard's model-status badge and ClientSettingsPanel's form both
// used to fetch it independently, doubling every request and every
// open/close of the settings modal).
import { getAgentsApiBaseUrl } from './apiBase';

export type ClientConfigData = Record<string, any>;

let cachedConfig: ClientConfigData | null = null;
let inFlight: Promise<ClientConfigData> | null = null;

async function requestClientConfig(): Promise<ClientConfigData> {
  const res = await fetch(`${getAgentsApiBaseUrl()}/client-config`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const resData = await res.json();
  return resData.config || resData;
}

export function getCachedClientConfig(): ClientConfigData | null {
  return cachedConfig;
}

// Returns the cached config unless `force` is set (used after a save/reset
// that just changed the backend's config) or nothing has been fetched yet.
export function fetchClientConfig(options: { force?: boolean } = {}): Promise<ClientConfigData> {
  if (!options.force && cachedConfig) {
    return Promise.resolve(cachedConfig);
  }
  if (!inFlight) {
    inFlight = requestClientConfig()
      .then(data => {
        cachedConfig = data;
        return data;
      })
      .finally(() => {
        inFlight = null;
      });
  }
  return inFlight;
}

export function invalidateClientConfig(): void {
  cachedConfig = null;
}
