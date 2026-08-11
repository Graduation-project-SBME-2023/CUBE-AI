# Architecture decision from contention testing

## Decision

Separate GPU inference from the application data API. Do not adopt the entire
proposed gateway/PostgreSQL/MongoDB design based on this test alone.

The existing control API on port 8001 already demonstrates the important
isolation boundary. Complete it and treat it as the starting point for the
application API instead of immediately creating a competing implementation.
MongoDB is not required to obtain the measured benefit.

## Problem reproduced in the current architecture

The MONAI Label process on port 8000 serves inference together with patient,
study, model, segmentation, chat, workflow, and synchronization routes. The
frontend still sends several CRUD/chat flows to port 8000. Both the MONAI
process and the control API run one Uvicorn worker.

The chat-session list route is also an independent capacity problem. It has no
pagination and eagerly loads every session's messages and studies. With the
test database's 146 sessions, one response was approximately 285 KB and took
about one second at rest. Replicating the endpoint in another service preserves
that query and serialization cost.

## Controlled experiment

The quick evaluator generated a synthetic 48x48x48 CT-like NIfTI volume and ran
four phases. No patient images were read and no CRUD records were written. The
data workload used one iteration per second, with four concurrent reads per
iteration: patients, studies, chat sessions, and models.

Run ID: `20260811-175805`

| Phase | Data target | Inference | Data p95 | Data average | Errors |
|---|---|---|---:|---:|---:|
| Monolith idle | `:8000` | Off | 2920 ms | 1140 ms | 0% |
| Control idle | `:8001` | Off | 1340 ms | 481 ms | 0% |
| Monolith contention | `:8000` | `:8000` | 7488 ms | 4297 ms | 0% |
| Control contention | `:8001` | `:8000` | 991 ms | 328 ms | 0% |

The monolith's p95 latency increased 2.56 times during inference. Under the same
inference workload, the separate control process's p95 was 7.55 times lower
than the monolith's. The synthetic inference request succeeded in both phases.

An earlier diagnostic run at three iterations per second saturated both data
APIs: idle failure rates were about 57%, monolith contention reached 91%, and
control contention reached 44%. That run found the low capacity ceiling, but it
is not the controlled A/B result because it preceded phase cooldowns and model
warm-up.

## What the evidence supports

1. Keep the OHIF viewer and Orthanc DICOMweb path independent.
2. Keep the Agents backend as its own service.
3. Make the application API own PostgreSQL CRUD, chat, workflow, Orthanc sync,
   and other orchestration that does not require GPU model execution.
4. Make the inference service own model loading, inference, and training only.
   It should not expose duplicate database CRUD routers.
5. Route frontend CRUD/chat calls and MCP database tools to the application API.
6. Use one public origin or a thin reverse proxy if a gateway is needed. A new
   stateful gateway service is not necessary just to obtain route isolation.

## What the evidence does not support

- It does not justify MongoDB. The measured bottleneck is process contention
  plus an unbounded/eager PostgreSQL-backed endpoint, not a demonstrated
  PostgreSQL storage limit.
- It does not justify rewriting all CRUD code under a new port 8100. Completing
  and hardening the existing port 8001 service is lower risk and is directly
  supported by the comparison.
- It does not show that service separation alone meets the target. Even the
  isolated control API had a 991-1340 ms p95 at only four requests per second.

## Required fixes before the final capacity decision

1. Add pagination to `GET /chat_sessions/` and return a lightweight session
   summary without `messages`; fetch messages only for one selected chat.
2. Add limits to every collection endpoint and enforce maximum page sizes.
3. Add endpoint timing, SQL query count/time, event-loop lag, and database pool
   wait metrics.
4. Configure and test an appropriate worker count for the application API.
5. Remove application routers from MONAI only after all callers use the control
   API and contract tests prove parity.
6. Run the standard profile after these fixes and collect CPU, memory, GPU,
   database-pool, and network metrics during every phase.

## Acceptance gates

- Data API error rate below 1% and no dropped iterations.
- Data API p95 below 500 ms for list/summary operations.
- Data API contention p95 no more than 1.25 times its idle p95.
- Inference failure rate below 1%, with an explicit concurrency/queue limit.
- Restarting or exhausting inference does not make CRUD/chat unavailable.
- PostgreSQL remains the system of record with tested transaction and migration
  behavior. Any MongoDB proposal requires its own ADR, ownership contract,
  failure-recovery test, and A/B benchmark.

## Reproduction

```powershell
# Validate the environment and warm the inference model
powershell -NoProfile -ExecutionPolicy Bypass `
  -File Backend/tests/load/evaluate-architecture.ps1 -Profile quick

# Decision run after API pagination/optimization
powershell -NoProfile -ExecutionPolicy Bypass `
  -File Backend/tests/load/evaluate-architecture.ps1 -Profile standard
```

Raw JSON summaries and the generated report are written to
`Backend/tests/load/results/` and intentionally ignored by Git.
