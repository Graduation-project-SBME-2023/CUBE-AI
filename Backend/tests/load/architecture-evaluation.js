import exec from 'k6/execution';
import http from 'k6/http';
import { check } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const DATA_URL = normalizeBaseUrl(__ENV.DATA_URL || 'http://host.docker.internal:8000');
const INFERENCE_URL = normalizeBaseUrl(__ENV.INFERENCE_URL || 'http://host.docker.internal:8000');
const ENABLE_INFERENCE = booleanValue(__ENV.ENABLE_INFERENCE, false);
const DATA_RATE = positiveInteger(__ENV.DATA_RATE, 5, 'DATA_RATE');
const DATA_DURATION = __ENV.DATA_DURATION || '30s';
const INFERENCE_VUS = positiveInteger(__ENV.INFERENCE_VUS, 1, 'INFERENCE_VUS');
const INFERENCE_MODEL = __ENV.INFERENCE_MODEL || 'segmentation_spleen';
const REQUEST_TIMEOUT = __ENV.REQUEST_TIMEOUT || '10s';
const INFERENCE_TIMEOUT = __ENV.INFERENCE_TIMEOUT || '5m';

const dataLatency = new Trend('data_latency', true);
const dataFailures = new Rate('data_failures');
const dataRequests = new Counter('data_requests');
const inferenceLatency = new Trend('inference_latency', true);
const inferenceFailures = new Rate('inference_failures');
const inferenceRequests = new Counter('inference_requests');

validateTargets();

const scenarios = {
  data_plane: {
    executor: 'constant-arrival-rate',
    exec: 'dataTraffic',
    rate: DATA_RATE,
    timeUnit: '1s',
    duration: DATA_DURATION,
    preAllocatedVUs: Math.max(10, DATA_RATE * 2),
    maxVUs: Math.max(50, DATA_RATE * 10),
  },
};

if (ENABLE_INFERENCE) {
  scenarios.inference_plane = {
    executor: 'per-vu-iterations',
    exec: 'inferenceTraffic',
    vus: INFERENCE_VUS,
    iterations: 1,
    startTime: '2s',
    maxDuration: INFERENCE_TIMEOUT,
  };
}

export const options = {
  discardResponseBodies: false,
  scenarios,
};

const syntheticNifti = ENABLE_INFERENCE ? createSyntheticNifti(48) : null;

export function setup() {
  const preflight = http.get(`${DATA_URL}/patients/?limit=1`, {
    timeout: REQUEST_TIMEOUT,
    tags: { workload: 'preflight', target: 'data' },
  });
  if (preflight.status !== 200) {
    exec.test.abort(`Data API preflight failed with HTTP ${preflight.status || 0}: ${preflight.error || ''}`);
  }

  if (ENABLE_INFERENCE) {
    const inferencePreflight = http.get(`${INFERENCE_URL}/info/`, {
      timeout: REQUEST_TIMEOUT,
      tags: { workload: 'preflight', target: 'inference' },
    });
    if (inferencePreflight.status !== 200) {
      exec.test.abort(
        `Inference API preflight failed with HTTP ${inferencePreflight.status || 0}: ${inferencePreflight.error || ''}`
      );
    }
  }

  return {
    dataUrl: DATA_URL,
    inferenceUrl: INFERENCE_URL,
    inferenceEnabled: ENABLE_INFERENCE,
    dataRate: DATA_RATE,
    dataDuration: DATA_DURATION,
    inferenceVUs: INFERENCE_VUS,
    inferenceModel: INFERENCE_MODEL,
  };
}

export function dataTraffic() {
  const requests = [
    ['GET', `${DATA_URL}/patients/?limit=20`, null, requestParams('patients')],
    ['GET', `${DATA_URL}/studies/?limit=20`, null, requestParams('studies')],
    ['GET', `${DATA_URL}/chat_sessions/?active_only=false`, null, requestParams('chat_sessions')],
    ['GET', `${DATA_URL}/models/`, null, requestParams('models')],
  ];

  const responses = http.batch(requests);
  for (let index = 0; index < responses.length; index += 1) {
    const response = responses[index];
    const endpoint = ['patients', 'studies', 'chat_sessions', 'models'][index];
    const failed = response.status !== 200;

    dataLatency.add(response.timings.duration, { endpoint });
    dataFailures.add(failed, { endpoint });
    dataRequests.add(1, { endpoint });
    check(response, { [`data ${endpoint}: status is 200`]: (r) => r.status === 200 }, { workload: 'data', endpoint });
  }
}

export function inferenceTraffic() {
  const response = http.post(
    `${INFERENCE_URL}/infer/${encodeURIComponent(INFERENCE_MODEL)}?output=all`,
    {
      params: JSON.stringify({ architecture_evaluation: true }),
      file: http.file(syntheticNifti, 'synthetic-ct.nii', 'application/octet-stream'),
    },
    {
      timeout: INFERENCE_TIMEOUT,
      responseType: 'none',
      tags: { workload: 'inference', model: INFERENCE_MODEL },
    }
  );

  const failed = response.status !== 200;
  inferenceLatency.add(response.timings.duration, { model: INFERENCE_MODEL });
  inferenceFailures.add(failed, { model: INFERENCE_MODEL });
  inferenceRequests.add(1, { model: INFERENCE_MODEL });
  check(
    response,
    { 'synthetic inference: status is 200': (r) => r.status === 200 },
    { workload: 'inference', model: INFERENCE_MODEL }
  );
}

function requestParams(endpoint) {
  return {
    timeout: REQUEST_TIMEOUT,
    responseType: 'none',
    tags: { workload: 'data', endpoint },
  };
}

function createSyntheticNifti(size) {
  const headerSize = 352;
  const voxelCount = size * size * size;
  const buffer = new ArrayBuffer(headerSize + voxelCount * 4);
  const view = new DataView(buffer);
  const bytes = new Uint8Array(buffer);

  view.setInt32(0, 348, true);
  view.setInt16(40, 3, true);
  view.setInt16(42, size, true);
  view.setInt16(44, size, true);
  view.setInt16(46, size, true);
  view.setInt16(48, 1, true);
  view.setInt16(50, 1, true);
  view.setInt16(52, 1, true);
  view.setInt16(54, 1, true);
  view.setInt16(70, 16, true);
  view.setInt16(72, 32, true);
  view.setFloat32(76, 1, true);
  view.setFloat32(80, 1, true);
  view.setFloat32(84, 1, true);
  view.setFloat32(88, 1, true);
  view.setFloat32(108, headerSize, true);
  view.setFloat32(112, 1, true);
  bytes[123] = 2;
  view.setInt16(254, 1, true);
  view.setFloat32(280, 1, true);
  view.setFloat32(300, 1, true);
  view.setFloat32(320, 1, true);
  bytes[344] = 110;
  bytes[345] = 43;
  bytes[346] = 49;
  bytes[347] = 0;

  const center = (size - 1) / 2;
  const radiusSquared = Math.pow(size * 0.22, 2);
  let offset = headerSize;
  for (let z = 0; z < size; z += 1) {
    for (let y = 0; y < size; y += 1) {
      for (let x = 0; x < size; x += 1) {
        const distanceSquared =
          Math.pow(x - center, 2) + Math.pow(y - center, 2) + Math.pow(z - center, 2);
        const value = distanceSquared <= radiusSquared ? 80 : -900;
        view.setFloat32(offset, value, true);
        offset += 4;
      }
    }
  }

  return buffer;
}

function validateTargets() {
  if (String(__ENV.ALLOW_REMOTE_TARGETS).toLowerCase() === 'true') {
    return;
  }
  const targets = ENABLE_INFERENCE ? [DATA_URL, INFERENCE_URL] : [DATA_URL];
  const remote = targets.filter((target) => !isLocalTarget(target));
  if (remote.length > 0) {
    throw new Error(
      `Refusing non-local targets: ${remote.join(', ')}. ` +
        'Set ALLOW_REMOTE_TARGETS=true only for an authorized environment.'
    );
  }
}

function isLocalTarget(value) {
  const match = /^https?:\/\/([^/:?#]+)/i.exec(value);
  if (!match) return false;
  const hostname = match[1].toLowerCase();
  return [
    'localhost',
    '127.0.0.1',
    '[::1]',
    'host.docker.internal',
    'monailabel',
    'control-api',
    'api-gateway',
    'inference-service',
  ].includes(hostname);
}

function normalizeBaseUrl(value) {
  return value.replace(/\/+$/, '');
}

function booleanValue(value, fallback) {
  if (value === undefined) return fallback;
  return String(value).toLowerCase() === 'true';
}

function positiveInteger(value, fallback, name) {
  const parsed = value === undefined ? fallback : Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer.`);
  }
  return parsed;
}
