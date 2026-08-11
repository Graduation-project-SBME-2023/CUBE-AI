# System load tests

This directory contains a [k6](https://grafana.com/docs/k6/) HTTP load test for
the deployed CUBE.AI/OHIF system. Each virtual user repeatedly exercises the
read-only application path:

1. OHIF frontend bootstrap (`GET /`)
2. Agent API schema (`GET /openapi.json`)
3. Control API health (`GET /health`)
4. MONAI Label metadata (`GET /info/`)
5. Orthanc DICOMweb study search (`GET /dicom-web/studies?limit=20`)

The test does not upload DICOM files, launch inference, run an agent pipeline,
or mutate application state. A preflight stops the run if an enabled service is
not returning HTTP 200.

## Quick start

Start the application first, then run the smoke profile from the repository
root. Docker is the default k6 runtime, so k6 does not need to be installed:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File Backend/tests/load/run.ps1 -Profile smoke
```

The available profiles are:

| Profile | Virtual users | Warm-up | Steady load | Cool-down |
|---|---:|---:|---:|---:|
| `smoke` | 1 | 2 seconds | 10 seconds | 2 seconds |
| `load` | 10 | 15 seconds | 2 minutes | 15 seconds |
| `stress` | 50 | 30 seconds | 5 minutes | 30 seconds |

Each virtual user sends one request to every enabled service per iteration and
then waits one second. A JSON summary is written under
`Backend/tests/load/results/`.
The command exits non-zero if preflight fails or a threshold is crossed.

Run the normal load profile:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File Backend/tests/load/run.ps1 -Profile load
```

To test only running backend services:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File Backend/tests/load/run.ps1 -Profile load `
  -EnabledServices agents,control,monai,orthanc
```

Use `-Runtime local` when k6 is installed on the host. The runner's defaults
match the repository's Docker Compose ports (`80`, `8150`, `8001`, `8000`, and
`8043`). Every URL can be overridden, for example:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File Backend/tests/load/run.ps1 -Profile smoke `
  -FrontendUrl http://localhost:3000
```

If Orthanc authentication is enabled, set `ORTHANC_USER` and
`ORTHANC_PASSWORD` in the shell before running the test.

## Pass/fail thresholds

The defaults are intended as an initial service-level objective baseline:

| Metric | Default threshold |
|---|---:|
| Failed requests, per service | less than 1% |
| Successful checks | more than 99% |
| Frontend p95 | under 1000 ms |
| Agent API p95 | under 1000 ms |
| Control API p95 | under 500 ms |
| MONAI Label p95 | under 2000 ms |
| Orthanc DICOMweb p95 | under 1500 ms |

Direct k6 runs can tune these values with environment variables:

- `MAX_ERROR_RATE`
- `P95_FRONTEND_MS`, `P95_AGENTS_MS`, `P95_CONTROL_MS`
- `P95_MONAI_MS`, `P95_ORTHANC_MS`
- `VUS`, `WARMUP`, `DURATION`, `COOLDOWN`, `THINK_TIME_SECONDS`
- `REQUEST_TIMEOUT`

For example, with a local k6 installation:

```powershell
k6 run -e VUS=20 -e DURATION=3m -e P95_MONAI_MS=2500 `
  Backend/tests/load/system-load-test.js
```

## Safety

The script accepts local and Docker-internal hostnames by default. It refuses
other targets to reduce the chance of accidentally load testing production or
a third-party service. Use `-AllowRemoteTargets` only when the system owner has
authorized the test and the target URLs are correct.

This HTTP suite measures API and DICOM query capacity; it does not benchmark
GPU inference or browser rendering. Those workloads need separate datasets and
resource-specific acceptance criteria so that patient data and expensive model
jobs are not triggered accidentally.

## Architecture evaluation

`system-load-test.js` is a deployment smoke/load suite. It is not evidence for
or against splitting the MONAI Label service. Use the architecture evaluator to
measure cross-workload interference:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File Backend/tests/load/evaluate-architecture.ps1 -Profile quick
```

The evaluator compares four phases:

| Phase | Data/CRUD target | Inference target |
|---|---|---|
| `monolith-idle` | MONAI `:8000` | disabled |
| `control-idle` | Control API `:8001` | disabled |
| `monolith-contention` | MONAI `:8000` | MONAI `:8000` |
| `control-contention` | Control API `:8001` | MONAI `:8000` |

The data workload sends patient, study, chat-session, and model reads at a
constant arrival rate. Inference uses a generated 48x48x48 CT-like NIfTI volume
and never reads patient data, trains a model, or writes CRUD records. Results
include data p95 latency, errors, dropped iterations, inference latency, and
three decision ratios. The generated Markdown report distinguishes evidence
for process isolation from evidence for the larger gateway/MongoDB proposal.

Use `-Profile standard` for the decision run after the quick test validates the
environment. `-SkipInference` runs only the two idle baselines. Override
`-MonolithUrl`, `-ControlUrl`, and `-InferenceUrl` to evaluate a candidate
deployment. Remote targets still require `-AllowRemoteTargets` and explicit
authorization.

The runner pauses between phases to avoid carrying queued requests into the
next result. Use `-ReuseTimestamp <yyyyMMdd-HHmmss>` to regenerate a report from
existing raw summaries without repeating the load. Because the first inference
after service startup includes model loading and CUDA warm-up, run the quick
profile before using the standard profile for the final comparison.
