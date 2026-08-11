import encoding from 'k6/encoding';
import exec from 'k6/execution';
import http from 'k6/http';
import { check, group, sleep } from 'k6';

const DEFAULT_URLS = {
  frontend: 'http://host.docker.internal',
  agents: 'http://host.docker.internal:8150',
  control: 'http://host.docker.internal:8001',
  monai: 'http://host.docker.internal:8000',
  orthanc: 'http://host.docker.internal:8043',
};

const SERVICE_NAMES = Object.keys(DEFAULT_URLS);
const ENABLED_SERVICES = parseEnabledServices(__ENV.ENABLED_SERVICES || SERVICE_NAMES.join(','));
const URLS = {
  frontend: normalizeBaseUrl(__ENV.FRONTEND_URL || DEFAULT_URLS.frontend),
  agents: normalizeBaseUrl(__ENV.AGENTS_URL || DEFAULT_URLS.agents),
  control: normalizeBaseUrl(__ENV.CONTROL_URL || DEFAULT_URLS.control),
  monai: normalizeBaseUrl(__ENV.MONAI_URL || DEFAULT_URLS.monai),
  orthanc: normalizeBaseUrl(__ENV.ORTHANC_URL || DEFAULT_URLS.orthanc),
};

const VUS = positiveInteger(__ENV.VUS, 10, 'VUS');
const THINK_TIME_SECONDS = nonNegativeNumber(__ENV.THINK_TIME_SECONDS, 1, 'THINK_TIME_SECONDS');
const REQUEST_TIMEOUT = __ENV.REQUEST_TIMEOUT || '10s';

const MAX_ERROR_RATE = probability(__ENV.MAX_ERROR_RATE, 0.01, 'MAX_ERROR_RATE');
const P95_FRONTEND_MS = positiveNumber(__ENV.P95_FRONTEND_MS, 1000, 'P95_FRONTEND_MS');
const P95_AGENTS_MS = positiveNumber(__ENV.P95_AGENTS_MS, 1000, 'P95_AGENTS_MS');
const P95_CONTROL_MS = positiveNumber(__ENV.P95_CONTROL_MS, 500, 'P95_CONTROL_MS');
const P95_MONAI_MS = positiveNumber(__ENV.P95_MONAI_MS, 2000, 'P95_MONAI_MS');
const P95_ORTHANC_MS = positiveNumber(__ENV.P95_ORTHANC_MS, 1500, 'P95_ORTHANC_MS');

validateTargets();

export const options = {
  discardResponseBodies: false,
  scenarios: {
    system_read_path: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: __ENV.WARMUP || '15s', target: VUS },
        { duration: __ENV.DURATION || '2m', target: VUS },
        { duration: __ENV.COOLDOWN || '15s', target: 0 },
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: buildThresholds(),
};

const commonParams = {
  timeout: REQUEST_TIMEOUT,
  redirects: 3,
};

export function setup() {
  const failed = [];

  for (const service of ENABLED_SERVICES) {
    const response = requestService(service, 'preflight');
    if (response.status !== 200) {
      failed.push(`${service} (${response.status || response.error || 'connection error'})`);
    }
  }

  if (failed.length > 0) {
    exec.test.abort(`Preflight failed: ${failed.join(', ')}`);
  }

  return { enabledServices: ENABLED_SERVICES };
}

export default function (data) {
  for (const service of data.enabledServices) {
    group(service, () => {
      const response = requestService(service, 'load');
      checkResponse(service, response);
    });
  }

  if (THINK_TIME_SECONDS > 0) {
    sleep(THINK_TIME_SECONDS);
  }
}

function requestService(service, phase) {
  const params = {
    ...commonParams,
    tags: { service, phase },
    responseType: service === 'frontend' ? 'text' : 'text',
  };

  switch (service) {
    case 'frontend':
      return http.get(`${URLS.frontend}/`, params);
    case 'agents':
      return http.get(`${URLS.agents}/openapi.json`, params);
    case 'control':
      return http.get(`${URLS.control}/health`, params);
    case 'monai':
      return http.get(`${URLS.monai}/info/`, params);
    case 'orthanc':
      return http.get(`${URLS.orthanc}/dicom-web/studies?limit=20`, {
        ...params,
        headers: orthancHeaders(),
      });
    default:
      throw new Error(`Unsupported service: ${service}`);
  }
}

function checkResponse(service, response) {
  const checks = {
    [`${service}: status is 200`]: (r) => r.status === 200,
    [`${service}: response is not empty`]: (r) => typeof r.body === 'string' && r.body.length > 0,
  };

  if (service === 'frontend') {
    checks['frontend: content type is HTML'] = (r) =>
      String(r.headers['Content-Type'] || '').toLowerCase().includes('text/html');
  } else if (service === 'control') {
    checks['control: reports healthy'] = (r) => jsonFieldEquals(r.body, 'status', 'healthy');
  } else if (service === 'agents' || service === 'monai' || service === 'orthanc') {
    checks[`${service}: response is valid JSON`] = (r) => isJson(r.body);
  }

  check(response, checks, { service });
}

function buildThresholds() {
  const thresholds = {
    checks: [`rate>${1 - MAX_ERROR_RATE}`],
  };

  const p95ByService = {
    frontend: P95_FRONTEND_MS,
    agents: P95_AGENTS_MS,
    control: P95_CONTROL_MS,
    monai: P95_MONAI_MS,
    orthanc: P95_ORTHANC_MS,
  };

  for (const service of ENABLED_SERVICES) {
    thresholds[`http_req_failed{service:${service},phase:load}`] = [`rate<${MAX_ERROR_RATE}`];
    thresholds[`http_req_duration{service:${service},phase:load}`] = [`p(95)<${p95ByService[service]}`];
  }

  return thresholds;
}

function orthancHeaders() {
  const headers = { Accept: 'application/dicom+json, application/json' };
  if (__ENV.ORTHANC_USER) {
    const credentials = `${__ENV.ORTHANC_USER}:${__ENV.ORTHANC_PASSWORD || ''}`;
    headers.Authorization = `Basic ${encoding.b64encode(credentials)}`;
  }
  return headers;
}

function parseEnabledServices(value) {
  const services = [...new Set(value.split(',').map((item) => item.trim().toLowerCase()).filter(Boolean))];
  const unknown = services.filter((service) => !SERVICE_NAMES.includes(service));

  if (services.length === 0) {
    throw new Error('ENABLED_SERVICES must contain at least one service.');
  }
  if (unknown.length > 0) {
    throw new Error(`Unknown ENABLED_SERVICES values: ${unknown.join(', ')}`);
  }

  return services;
}

function validateTargets() {
  if (String(__ENV.ALLOW_REMOTE_TARGETS).toLowerCase() === 'true') {
    return;
  }

  const remote = ENABLED_SERVICES.filter((service) => !isLocalTarget(URLS[service]));
  if (remote.length > 0) {
    throw new Error(
      `Refusing non-local targets for ${remote.join(', ')}. ` +
        'Set ALLOW_REMOTE_TARGETS=true only when you are authorized to load test them.'
    );
  }
}

function isLocalTarget(value) {
  const match = /^https?:\/\/([^/:?#]+)/i.exec(value);
  if (!match) {
    return false;
  }

  const hostname = match[1].toLowerCase();
  return (
    hostname === 'localhost' ||
    hostname === '127.0.0.1' ||
    hostname === '[::1]' ||
    hostname === 'host.docker.internal' ||
    hostname === 'frontend' ||
    hostname === 'ohif_viewer' ||
    hostname === 'agents' ||
    hostname === 'control-api' ||
    hostname === 'monailabel' ||
    hostname === 'orthanc'
  );
}

function normalizeBaseUrl(value) {
  return value.replace(/\/+$/, '');
}

function isJson(value) {
  try {
    JSON.parse(value);
    return true;
  } catch (_) {
    return false;
  }
}

function jsonFieldEquals(value, field, expected) {
  try {
    return JSON.parse(value)[field] === expected;
  } catch (_) {
    return false;
  }
}

function positiveInteger(value, fallback, name) {
  const parsed = value === undefined ? fallback : Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer.`);
  }
  return parsed;
}

function positiveNumber(value, fallback, name) {
  const parsed = value === undefined ? fallback : Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive number.`);
  }
  return parsed;
}

function nonNegativeNumber(value, fallback, name) {
  const parsed = value === undefined ? fallback : Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${name} must be zero or greater.`);
  }
  return parsed;
}

function probability(value, fallback, name) {
  const parsed = value === undefined ? fallback : Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0 || parsed >= 1) {
    throw new Error(`${name} must be greater than 0 and less than 1.`);
  }
  return parsed;
}
