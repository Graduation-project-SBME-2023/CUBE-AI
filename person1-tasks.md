# Person 1 — Compute jobs and MONAI: task breakdown

Breakdown of Person 1's area from [team-plan.md](team-plan.md) into small, PR-sized tasks.
Plan, phase and problem ids (Plan 5, Phase 15.2, J1, H12, T4, D24 …) refer to [plan.md](plan.md).

**Branch:** `track/p1-compute` off `New-Architecture`; one small PR per task (or per 2–3 tightly
related tasks), each passing the deterministic gate.

**Order** (from `team-plan.md`): Phase 5.1 → Phases 7.1 (ports), 7.3 → Plan 15 → the rest of
Plan 5 → Phases 7.1 (job manifests), 7.4, 7.5 → Plan 16. **Waits:** none.

**Others wait on you** — merge these early:

| What | Who uses it | Task |
|---|---|---|
| `JobClient` contract + in-memory backend | Persons 2, 3 (H13 skip uses `JobClient.list`) | A1–A4 |
| Ports + `graph` visibility of the heavy tools, `run_modeling` | Person 5's flow, the first working flow | B1–B5 |
| `MonaiHttpBackend` as a `JobClient` | contract table | C6 |
| Segmentation client on `MonaiHttpBackend` (Phase 15.4) | Person 2, Phase 9.7 | C7–C9 |

**Where the code stands today** (checked in the repo):

- `Backend/jobs_core/` and `Backend/compute_worker/` do not exist yet.
- `toolpacks/common/polling.py` already has `RetryPolicy`, `PollTimeout`, `poll_until` (to move in A3).
- `toolpacks/common/manifests.py` has the `INTERIM_HEAVY` preset; it is used by
  `modeling/manifest.py` (5 tools), `radiomics/extract_features.py`, `radiomics/analyze_importance.py`,
  `segmentation/run_segmentation.py`, `segmentation/run_with_guard.py`. All are `visibility={"agent"}`.
- Artifact types `Cohort`, `MaskSet`, `FeatureTable`, `FeatureImportance`, `ModelingOutput` exist in
  `toolkit/artifacts/builtin.py`.
- `run_attempts` already has `dispatch_kind` (`in_process | job | inline | legacy`) and
  `submission_key` (unique). `application_api/api/routes/node_runs.py` already has the attempt routes
  `/dispatched`, `/running`, `/complete`, `/fail`, `/cancel`, `/stopped`, `/reconcile`, `/suspend`,
  `/resume` — but **no** `/dispatch` (job) or `/job` read route.
- `toolkit/runtime/dispatch.py` routes by `spec.execution` through `self.runners`; a `job` runner is
  not registered yet (`execution_unavailable`).
- Alembic head: `r1f0a2c3d4e5`. Migration "s" goes after it; Person 3's "t" — whichever merges
  second sets its `down_revision` to the head at that time.
- `application_api/api/routes/training_jobs.py` exists (POST / GET list / GET one) — Plan 16.2 builds on it.

---

## Stage A — Phase 5.1: Job contract and client library (`Backend/jobs_core/`) — first days

Pure Python, no Procrastinate, no HTTP. This is a **contract**: shapes final, logic may be simple.

### A1. Package skeleton + `contract.py`
- **Files:** `Backend/jobs_core/__init__.py`, `contract.py`.
- **Do:**
  - `JobRequest` (Pydantic): `job_id`, `submission_key`, `attempt`, `job_name` (a registered tool
    name, never an import path), `job_version`, `args` (JSON only), `context`
    (`ToolContext.to_payload()`), `resource_class`, `timeout_s`, `queue_timeout_s`, `priority`.
  - Helpers: `canonical_json(args)`, `make_job_id(run_id, job_name, args) = sha256(run_id|job_name|canonical_json)`,
    `make_submission_key(job_id, attempt_number) = f"{job_id}.{attempt_number}"`.
    (No resource→queue helper: `ToolSpec.resource_class` in `toolkit/tools/spec.py:196` already
    derives it from `RESOURCE_QUEUES`; D10 copies it into the request.)
  - `JobRecord`: `id, status, error_code, attempt, input_hash, progress, queued_at, started_at,
    finished_at, error, result | result_ref, host`.
  - `ErrorCode` literal: `error, timeout, queue_timeout, oom, interrupted, cancelled, run_not_running`
    (+ `resource_unavailable` used by the MONAI backend, C6).
- **Tests:** `tests/unit/jobs/test_contract.py` — id stable across key order, differs on args;
  one submission key per attempt; args must be JSON-serialisable.

### A1b. Contract follow-up (decisions of 2026-10-22: one submit, MONAI owns the GPU)
- **Files:** `jobs_core/contract.py`, `tests/unit/jobs/test_contract.py`, `test_contract_matches_toolkit.py`.
- **Do:**
  - `JobRequest.reusable: bool` (required): true only for a job that writes nothing but its result.
  - `RESOURCE_CLASSES` = `compute.cpu`, `compute.highmem`, `monai.gpu` (no `compute.gpu`); the
    toolkit test compares the `compute.*` part with `RESOURCE_QUEUES`.
  - `ErrorCode.PROTOCOL_ERROR` (`protocol_error`, under `FAILED` in `ERROR_CODES_BY_STATUS`) and a
    `JobProtocolError` exception for a status string a backend does not know.
  - `job_name` docstring: a registered **job** name (an `execution="job"` tool or a MONAI kind, `monai_infer`).
- **Toolkit (not your file — tell its owner):** drop `(GPU, "compute.gpu")` from `RESOURCE_QUEUES`;
  the catalog refuses a tool with `gpu` in `resources` and `execution="job"`.

### A2. `status.py` + `derive_status(row, job_dir)`
- **Files:** `jobs_core/status.py`, `jobs_core/job_dir.py` (read `request.json`, `progress.json`,
  `process.json`, `result.json`).
- **Do:** statuses `QUEUED RUNNING CANCELLING SUCCEEDED FAILED TIMED_OUT CANCELLED INTERRUPTED`;
  `TERMINAL`, `ACTIVE`, `is_final(status: JobStatus)` — **only known terminal statuses are final**
  (an unknown string is a `JobProtocolError`, never "final"). `derive_status` is the **only** mapping:
  `result.json` wins (only if its `submission_key` + `attempt_id` match) → `todo`=QUEUED →
  `doing`+live worker heartbeat **and** live job heartbeat (`process.json` `heartbeat_at`) =
  RUNNING/CANCELLING → either heartbeat stale (`JOB_HEARTBEAT_TIMEOUT_S`) = INTERRUPTED (progress is
  never a heartbeat) → `cancelled` before start = CANCELLED or TIMED_OUT(`queue_timeout`) → `failed`
  without `result.json` = FAILED(`error`) → no row = not found.
  Atomic `write_result()` (temp file + rename, stamps `submission_key` + `attempt_id`) lives here too,
  so the worker and tests share it.
- **Tests:** `test_derive_status.py` — table-driven, one row per rule; a `result.json` of another
  attempt is ignored; `is_final` refuses a raw string.

### A3. Move polling into `jobs_core`, add `budgets.py`
- **Files:** `jobs_core/polling.py` (moved from `toolpacks/common/polling.py`), `jobs_core/budgets.py`.
- **Do:** keep `RetryPolicy` (backoff, jitter, cap, `Retry-After`) and `poll_until()`; make the
  deadline **monotonic and pause-aware** and check `done()` before the deadline (S2). Update the
  callers in `toolpacks/` (keep a thin re-export in `common/polling.py` only if a pack can't import
  `jobs_core` through `_toolkit.py` — check the import-root rule first). `JobBudgets(queue_s, run_s,
  result_s, unavailable_s)` with `outer = sum + margin`.
  (Waiting on a Procrastinate job does **not** poll — it reads the SSE job stream, D11. `poll_until`
  stays for MONAI, C6; `RetryPolicy` also backs off the stream's reconnects.)
- **Tests:** `test_polling.py` — `Retry-After` honoured, cap, paused time not counted; `test_budgets.py`.

### A4. `client.py` protocol + `backends/in_memory.py`
- **Files:** `jobs_core/client.py`, `jobs_core/backends/__init__.py`, `backends/in_memory.py`.
- **Do:** `JobClient` Protocol, **one for every backend**: `submit(request: JobRequest)`, `get`,
  `wait(budgets, on_progress, cancel)`, `result`, `cancel`, `purge`, `list(run_id, active_only)`.
  `submit` rules: same key → existing record; same `job_id` + different `input_hash` → conflict;
  a `reusable` request whose `job_id` has a `SUCCEEDED` record from an earlier attempt → that record,
  anything else runs again under the new key; `queue_full` → retry within `unavailable_s`, then
  `resource_unavailable`. `wait`: a `JobProtocolError` keeps waiting (logged); at the deadline,
  cancel + fail with `protocol_error`. In-memory backend with a scriptable runner (tests drive
  status changes), usable as a reusable or a non-reusable backend.
- **Tests:** `test_in_memory_backend.py` — dedupe, conflict, reuse (reusable only), queue full,
  protocol error at the deadline, cancel queued/running, `list(active_only)`.
- **Done (Phase 5.1):** contract PR merged; your row in the Contracts table of `team-plan.md` updated.

---

## Stage B — Phase 7.1 (ports, `graph` visibility) + Phase 7.3 `run_modeling` — still inline

Everything stays `INTERIM_HEAVY` / inline here so the first working flow can run modeling.

### B1. Ports + `graph` visibility for the 5 modeling tools
- **Files:** `toolpacks/modeling/classification.py`, `regression.py`, `inference.py`/`evaluation.py`,
  `refit.py`, `manifest.py`.
- **Do:** `train_classifier`, `train_regressor`: `visibility={"graph"}`, input
  `features: FeatureTable` → output `modeling: ModelingOutput`. `run_classifier_inference`,
  `run_regression_inference`, `refit_model`: `modeling: ModelingOutput` (+ `features`) →
  `modeling: ModelingOutput`. `PortSpec` input `param` = the handler argument; output `extract`.
- **Tests:** catalog `validate()` passes; port extract test per tool on a recorded success result.
- **Check:** does anything still need `"agent"` visibility (the frozen AutoGen agents don't run tools,
  so `graph` only is what the plan says).

### B2. Ports for the 2 radiomics heavy tools
- **Files:** `toolpacks/radiomics/extract_features.py`, `analyze_importance.py`.
- **Do:** `visibility={"agent","graph"}`; extract: `cohort: Cohort`, `masks: MaskSet` →
  `features: FeatureTable`; importance: `features: FeatureTable` → `importance: FeatureImportance`.
- **Tests:** port extract tests; Definition of Ready table still green.

### B3. `run_modeling` input model + `auto` task rule
- **Files:** `toolpacks/modeling/run_modeling.py` (new).
- **Do:** params `feature_set_ids: list[str]`, `target`, `task` (`classification|regression|auto`),
  `comparisons`, `folds`, `seed`, `include_inference`. `auto`: one fixed, documented rule on the
  target column dtype (e.g. non-numeric or ≤ N distinct integer values → classification).
- **Tests:** the `auto` rule, table-driven.

### B4. `run_modeling` handler + manifest
- **Do:** manifest per system §15: version 1, input `features: FeatureTable` (many) → param
  `feature_set_ids`; output `result: ModelingOutput`; `side_effects=compute`, `cost=medium`,
  `resources={cpu_heavy}`, `phi=false`, `ui=modeling_results_view`, `visibility={"graph"}`
  (`execution` stays inline until E1, then `job`). Handler: engine (`modeling/engine/`) in order
  train → inference/evaluation → select, off the loop (`asyncio.to_thread`); writes `model_results`
  with producer `run_modeling` through `domain_api` (D24 attempt header).
- **Tests:** order of steps, producer name sent (`fake_domain_api`), success + error case in the
  Definition of Ready table.

### B5. `on_result` hook for `modeling_output`
- **Files:** `toolpacks/common/views.py` (or a modeling hook module).
- **Do:** copy the view-event logic from `services/pipeline_monitor.py:182-252` (don't import the
  frozen file); attach to `run_modeling` and the training tools via the spec, so it doesn't depend
  on the tool name (`:227` matches `pycaret_train_classifier`).
- **Tests:** golden test on a recorded payload (`tests/manual/view_payload_harness.py` shape) so
  `ModelingView` keeps working.

---

## Stage C — Plan 15: MONAI inference jobs + segmentation client

### C1. Phase 15.1 — safety net for `monailabel/endpoints/inference_jobs.py`
- **Files:** `tests/unit/monai/test_inference_jobs.py` (new; synthetic, no GPU, fake `/infer`).
- **Do:** submit, poll, resubmit after each terminal state, cancel while queued, restart scan.
  The resubmit test **reproduces J1** (marked `xfail(strict=True)` until C2).
- **Note:** MONAI image is Python 3.10 — no PEP 695 syntax in this code or its tests.

### C2. Phase 15.2 — J1, J2, J11 (slot accounting)
- **Files:** `monailabel/endpoints/inference_jobs.py`, `job_root.py`.
- **Do:** J1: explicit re-queue (`attempt + 1`), write the state **before** `reserve()`, release
  on any error. J2: keep the slot until `/infer` returns — a cancel does **not** close the loopback
  call — persist `CANCELLING → CANCELLED`; a call still running `INFERENCE_STUCK_S` after its cancel
  → MONAI reports unhealthy and exits (restart frees the GPU). J11: `CANCELLING` persisted.
  **J5 — one GPU gate** (`services/gpu_gate.py` replaces `inference_capacity`): inference and
  training take the same gate (`GPU_MAX_CONCURRENCY` running + `GPU_QUEUE_CAPACITY` waiting, then
  429); training preparation runs before the gate, not inside it.
- **Tests:** J1 test green; slot count back to 0 after 10 resubmits; J2 slot held during cancel;
  inference + training never hold the GPU at once; stuck call → unhealthy.

### C3. Phase 15.2 — J4, J6, J12 (status vocabulary, queue timeout, progress)
- **Do:** MONAI status strings = `jobs_core.status` (contract test compares them; MONAI can't import
  `jobs_core` on 3.10 if it uses 3.13 syntax — keep `status.py` 3.10-compatible, or duplicate the
  set and test equality — A1 already keeps `status.py` 3.10-compatible). J6: `queue_timeout_s` →
  `TIMED_OUT(queue_timeout)`. J12: progress, heartbeat, queue position fields + a list endpoint.
  GPU OOM: `torch.cuda.OutOfMemoryError` → `FAILED` with `error_code="oom"` (one line in `error`,
  traceback in the log).
- **Tests:** status-string contract; queue timeout; list endpoint; CUDA OOM → `oom`.

### C4. Phase 15.2 — J8, J10, J3
- **Do:** J8: TTL sweeper + start-up cleanup of `.result.tmp`. J10: blocking file I/O via
  `to_thread`, directories created once. J3: verify whether `/infer` blocks the loop in the 0.8.5
  image; if yes, run it in a thread. Write the J3 finding in `plan.md` (as-built note).
- **Tests:** TTL sweep; leftover tmp removed at start-up.

### C5. Phase 15.2 — J9 ingress
- **Files:** `Frontend/OHIF-Viewer/platform/app/.recipes/OpenResty-Orthanc/config/nginx.conf`, MONAI endpoints.
- **Do:** deny `/inference/restart`, the job endpoints **and MONAI Label's `/train/`** from the
  browser (the browser calls it today, `MonaiLabelClient.js:84`, and it takes no GPU slot); internal
  token on them. Training then starts only through F4, inside the GPU gate.

### C6. Phase 15.3 — `jobs_core/backends/monai_http.py` (`MonaiHttpBackend`)
- **Do:** implements `JobClient` with the **same `submit(JobRequest)`** as every backend
  (`job_name="monai_infer"`, `resource_class="monai.gpu"`, `reusable=true`, `args` = pair_id, model,
  image, prompt); MONAI's job id = `request.job_id`, and MONAI stores `request.attempt`, so a later
  attempt re-attaches to a finished pair job and a failed one is re-queued (J1); unknown MONAI status
  → `JobProtocolError`; honours `Retry-After`; closed status set; separate budgets;
  streams the result download with retry; cancel + purge in `finally`; 429 beyond `unavailable_s` →
  `error_code="resource_unavailable"`. Uses the shared HTTP client (`common/http_client.shared_client()`)
  — check whether `jobs_core` may use it or needs its own injected client.
- **Tests:** `respx` backend tests incl. 429 → `resource_unavailable`, a long pause keeps a
  finished result. Contract PR (Contracts table row).

### C7. Phase 15.4a — `services.py` on the backend + D10 job ids
- **Files:** `agents/execution_agents/segmentation/services.py`, `module.py`, `batch_store.py`.
- **Do:** `_await_inference_job` → the routing client (Phase 5.4) → `MonaiHttpBackend` (J7, J13,
  S1, S3, S4). The pair job is a `JobRequest`: `inference_job_id = make_job_id(run_id, "monai_infer",
  {pair_id, model, image, prompt})` — the one id rule, so **no correlation id** is stored; every
  attempt of a run re-attaches, a new run gets new ids (S5, `module.py:1440-1442`). Check that a
  batch never spans two node runs (if it can, put the batch id in `args`). Leave `_wait_for_review` as it is (S10 is Person 2's).

### C8. Phase 15.4b — heartbeat, threads, session handle
- **Files:** `segmentation/pipelines/common.py`, `services.py`, `module.py`.
- **Do:** J14/S2: heartbeat wrapper `finally: cancel + gather` (no leaked inner task). S7: heavy
  numpy/SimpleITK (`services.py:462,571,763`, `module.py:446-455`) → `to_thread`. S9: pass the
  session handle explicitly (no swallowed `ImportError`/`KeyError` at `common.py:133-134,152-153`);
  the Executor's cancel token reaches the MONAI cancel.

### C9. Phase 15.4c — `toolpacks/segmentation/run_segmentation.py` on the backend
- **Do:** the tool (and `run_with_guard.py` if it calls MONAI directly) submits through the routing
  client (→ `MonaiHttpBackend`); one MONAI client left (J13).
- **Tests:** `tests/unit/segmentation/` — node cancel reaches MONAI; a re-call after a simulated
  restart does not resubmit a finished pair job.
- **Done (Plan 15):** J1 test green, both clients on `MonaiHttpBackend`. **Merge now — Person 2's 9.7 needs it.**

---

## Stage D — the rest of Plan 5 (Procrastinate job platform)

### D1. Phase 5.2 — dependencies + Procrastinate `App` in the Application API
- **Do:** `procrastinate` (pinned) + `psycopg[binary,pool]` in the application-api requirements;
  a `PsycopgConnector` with DSN from `DATABASE_URL` minus `+asyncpg`, opened/closed in the
  lifespan of `application_api/main.py`.

### D2. Phase 5.2 — migration "s" (`s1f0a2c3d4e5_v1_s_job_queue`)
- **Files:** `application_api/migrations/versions/`, the PostgreSQL init script under `deploy/`.
- **Do:** Procrastinate schema SQL of the pinned version, unchanged; view `job_execution_status`
  (`submission_key` = `queueing_lock`, queue, status, queued/started/finished, worker id, last
  heartbeat); grants on Procrastinate objects only to role `cubeai_jobs` (created by the init
  script from `JOBS_DB_PASSWORD`). `down_revision` = head at merge time (coordinate with Person 3's "t").
- **Tests (contract):** the view exists with the expected columns; `cubeai_jobs` rights limited.

### D3. Phase 5.2 — T4 dispatch + T7 cancel for jobs
- **Files:** `api/routes/node_runs.py`, the run-attempt repository, `services/fencing.py` usage.
- **Do:** `POST /runs/{run_id}/attempts/{attempt_id}/dispatch` (attempt path, `Depends(fenced_db)`):
  require current attempt, `dispatch_kind='job'`, `preparing` (resend on `submitted`/`running` is a
  no-op); defer `compute.run_tool_job` with the stored request, `queue = resource_class`,
  `queueing_lock = lock = submission_key`, `retry=False` — **send, then commit** `submitted` +
  `dispatched_at` (defer on Procrastinate's connection, before the T4 transaction).
  `AlreadyEnqueued` = sent. `/dispatched` stays for `in_process`/`inline`. T7: `/cancel` on a job
  attempt cancels a `todo` job (writes its `CANCELLED` result + `NOTIFY job_events` `finished`),
  aborts a `doing` one.
  **The race is allowed:** a worker may call `/running` before the T4 commit — `mark_running`
  already accepts `preparing` (`run_attempts.py:232`) and the late commit is then a no-op
  (`mark_dispatched`, `:207`); don't "fix" that. **Admission** before the send: `JOB_QUEUE_CAPACITY`
  `todo` jobs per queue, `JOB_SESSION_CAPACITY` `todo`+`doing` per session → `429 queue_full` +
  `Retry-After` (the attempt stays `preparing`).
- **Tests:** live-DB suite (`run_live_db_tests.ps1`): resend changes nothing; cancel todo never starts;
  `/running` before the T4 commit works and the commit changes nothing; crash between send and
  commit is recovered; both capacity limits refuse with 429.

### D4. Phase 5.2 — read routes + the SSE job stream
- **Do:** `GET /runs/{run_id}/attempts/{attempt_id}/job` (view row, `owned_run`);
  `GET /jobs/queues` (per queue: `todo`, `doing`, live workers). New router registered in `main.py`.
- **Job stream** `GET …/job/events` (`text/event-stream`, `owned_run`, header `X-Accel-Buffering: no`):
  one `LISTEN job_events` connection per API worker process (lifespan), routing by `submission_key`;
  frames `state` (always first) → `progress` (`id` = seq) → `finished` (then close); `: ping` every
  `JOB_SSE_PING_S`; on a re-opened `LISTEN` connection, resend `state` on every open stream.
- **Tests:** stream opens with the current state; already-final job → `state` + `finished` at once;
  a notification reaches only its own key's streams; `LISTEN` reconnect resends state.

### D5. Phase 5.3 — `compute_worker/app.py` + `launcher.py`
- **Do:** `App(connector=PsycopgConnector(conninfo=JOBS_DATABASE_URL))`, JSON args only, one task
  `compute.run_tool_job(request)` with `retry=False`. Launcher: `create_subprocess_exec(...,
  start_new_session=True)`, env **allowlist** (no `MODEL_API_KEY`, no `JOBS_DATABASE_URL`, no
  tokens; `CUDA_VISIBLE_DEVICES=""` — MONAI owns the GPU), stdout/stderr to the job dir,
  `stop(grace_s)`: `killpg(SIGTERM)` → wait → `killpg(SIGKILL)` → reap, and return **only when
  `killpg(pgid, 0)` finds nobody left**; still there after a second grace → `StopFailed` (the
  supervisor records `INTERRUPTED`, the worker exits so compose restarts it).
- **Tests:** no secret reaches the child; stop reaches grandchildren; SIGTERM→grace→SIGKILL order;
  `stop` doesn't return while a group member lives; `StopFailed`.

### D6. Phase 5.3 — `supervisor.py`
- **Do:** in order: complete `result.json` **of this `submission_key` + `attempt_id`** → return
  (another identity → move aside, log, run); queue timeout → `TIMED_OUT(queue_timeout)`;
  `POST …/running` (refusal → `run_not_running`, H20, no child); launch → `process.json`
  (`host, pid, pgid`); relay progress from the child's pipe → atomic `progress.json` (with `seq`) +
  `NOTIFY job_events` (coalesced to one per `JOB_PROGRESS_NOTIFY_S`, latest never dropped, payload
  < 8000 bytes); watch every `JOB_POLL_S` (write the **job heartbeat** `heartbeat_at` in
  `process.json`, then `cancel_requested_at`, Procrastinate abort, `timeout_s`); exit → atomic
  `result.json` (stamped with `submission_key` + `attempt_id`) + `NOTIFY` `finished`, return
  normally; failure classes from the child's structured failure (`{class, message}`, traceback only
  in the job log): `oom` (`MemoryError`, or exit 137 without stop) → `FAILED(oom)`, unclassified →
  `FAILED(error)`; `CancelledError` → stop group in `except` + `finally`, `CANCELLED` (abort) or
  `INTERRUPTED` (shutdown), + `finished`.
- **Tests:** fake runner: short-circuit (same identity only), OOM mapping, kill order,
  `run_not_running`; progress relay coalescing and the cut message; heartbeat written each tick.

### D7. Phase 5.3 — `child.py`
- **Do:** `ensure_storage_layout()` + `install_write_guard(job_dir=...)` (inside `COMPUTE_JOBS_DIR`
  only its own job dir is writable — best effort: the audit hook misses native writes, there is no
  sandbox), per-job log handler (fixes H6); structured failure `{class, message}` on error;
  thread caps (`OMP/OPENBLAS/MKL_NUM_THREADS`, `COMPUTE_CPU_THREADS`); `ToolContext.from_payload()`
  with a `PipeProgressSink` (one JSON line per update to the supervisor; the child never writes
  `progress.json` and has no DB); catalog lookup — **allowlist of `execution="job"`
  tools only**; run the same handler; always write a result, even on error.
- **Tests:** the child can write its own job dir, not another job's; the guard is installed.

### D8. Phase 5.3 — periodic tasks, start-up recovery, `COMPUTE_JOBS_DIR`
- **Do:** periodic tasks on `compute.cpu`: stale heartbeat → failed (reads `INTERRUPTED`); `todo`
  past `queue_timeout_s` → result then cancel; retention (`JOB_RETENTION_HOURS`) for rows and job
  dirs **only when the attempt is terminal in `run_attempts`** (an open attempt keeps its dir, however
  old — an Agent System down for a weekend still finds its result). Start-up: kill process groups in `process.json` on this host with no live worker, write
  `INTERRUPTED`. Every one of these that ends a job sends `NOTIFY job_events` `finished`. Add `COMPUTE_JOBS_DIR` to `Backend/storage_paths.py` (keep it 3.10-compatible)
  and document it in `Backend/storage/README.md`.

### D9. ~~Phase 5.3 — GPU queue~~ — dropped (2026-10-22)
MONAI owns the GPU: no `compute.gpu` queue, no `compute-worker-gpu`, no GPU-index pool. If a
Procrastinate GPU queue ever comes back, it must use a GPU MONAI doesn't, and its index pool must
hold across worker processes (enforced single worker per machine, or a shared lock).

### D10. Phase 5.4 — `toolkit/runtime/job.py` + `backends/router.py` + dispatch routing
- **Do:** build `JobRequest` from spec + context (`reusable` = the tool has no write scopes);
  `submit → wait` (progress → `EventSink` as `tool_progress`) `→ result` (same JSON as inline);
  cancel token → `JobClient.cancel`; `purge` in `finally`. Register the `job` runner in
  `dispatch.py`. **`RoutingJobClient`** (`jobs_core/backends/router.py`): one `JobClient` that picks
  the backend by `resource_class` (`compute.*` → `AppApiJobClient`, `monai.gpu` →
  `MonaiHttpBackend`); unknown class refused at `submit`. `runtime/job.py` and the segmentation
  client (C7) both hold the router, never a backend.
- **Tests:** with the in-memory backend; router sends each class to its backend, refuses an unknown one.

### D11. Phase 5.4 — `jobs_core/backends/app_api.py` (`AppApiJobClient`)
- **Do:** `submit → …/dispatch`; `get → …/job` + job dir via `derive_status`; `wait → the SSE job
  stream` (`…/job/events`): `progress` → `on_progress` as it arrives (H19), `finished` ends it; a
  dropped stream reconnects with `RetryPolicy` backoff + `Last-Event-ID` (drop frames already seen);
  budgets stay the deadlines. **No polling.** `cancel → …/cancel`; `result → result.json` (refused
  unless its `submission_key` + `attempt_id` are the attempt's);
  `purge` deletes the job dir after T6; `list → the run's job attempts`.
- **Tests:** `respx` (scripted `text/event-stream` bodies) + a temp job dir: progress order,
  `finished` ends the wait, reconnect + dedupe, a budget deadline fires on a silent stream.

### D12. Phase 5.5 — deploy and CI
- **Do (one shared file per PR, rule 5):**
  - requirements: `procrastinate`, `psycopg[binary,pool]` in agents + application-api; remove
    `fastmcp`; no celery/redis/fakeredis. `Backend/agent_system/requirements.txt` is **UTF-16 LE, CRLF**.
  - `deploy/docker-compose.backend.gpu.yml`: `compute-worker-cpu` (agents image,
    `procrastinate --app=compute_worker.app.app worker --queues=compute.cpu --concurrency=${COMPUTE_MAX_CONCURRENCY:-1}`,
    `JOBS_DATABASE_URL`, `APP_API_URL`, same `/storage` mount, non-root, `mem_limit`, `init: true`,
    `stop_grace_period > JOB_KILL_GRACE_S`); **no** `compute-worker-gpu` (MONAI owns the GPU);
    remove the `Backend/mcp` bind mount.
  - `deploy/Dockerfile.agents`: copy `jobs_core`, `compute_worker`; remove `COPY ./Backend/mcp`.
  - `deploy/.env.example`: `JOBS_DB_PASSWORD, JOBS_DATABASE_URL, COMPUTE_MAX_CONCURRENCY,
    COMPUTE_CPU_THREADS, JOB_RETENTION_HOURS, JOB_HEARTBEAT_TIMEOUT_S, JOB_KILL_GRACE_S, JOB_POLL_S,
    JOB_PROGRESS_NOTIFY_S, JOB_SSE_PING_S, JOB_QUEUE_CAPACITY, JOB_SESSION_CAPACITY, COMPUTE_MEMORY_LIMIT`.
  - `pytest.ini`: `celery` marker → `jobs`; remove `sandbox`.
  - `CLAUDE.md`: the `JOBS_DATABASE_URL` exception to the DB boundary.
- **Tests (contract):** compose DB-boundary test; nothing outside `compute_worker/` and the API
  imports `procrastinate`; no redis/celery anywhere.

### D13. Phase 5.5 — jobs integration suite + CI
- **Files:** `tests/integration/jobs/`, `backend-tests.yml` (`live-db-tests` job, worker from the built image).
- **Do:** submit→progress→result (progress over the SSE stream while the job runs); a lost
  notification does not hang a waiter (`LISTEN` reconnect resends state); cancel queued; cancel running with child + grandchild; SIGTERM-ignoring
  child killed after grace; abort reaches supervisor `finally`; soft/hard timeouts; late cancel is a
  no-op; worker killed mid-job → `INTERRUPTED`; killed after `result.json` → no recompute;
  `run_not_running`; stale `attempt_id` write rejected; write guard active in the child; live worker
  + dead job heartbeat → `INTERRUPTED`; retention keeps an open attempt's dir.
- **Done (Plan 5):** unit + integration suites green; `runtime/job.py` works on both backends;
  no Redis/Celery left.

---

## Stage E — Phases 7.1 (job manifests), 7.4, 7.5: the 8 heavy tools become jobs

### E1. Job manifests
- **Files:** `toolpacks/common/manifests.py`, `modeling/manifest.py`, `radiomics/*.py`, `run_modeling.py`.
- **Do:** add a job preset (`execution="job"`, `side_effects="compute"`, `cost="heavy"`,
  `resources={"cpu_heavy"}`, `trust="first_party"`, `isolation="process"`) for the 7 heavy tools;
  `run_modeling` → `execution="job"`, `cost="medium"`. Delete `INTERIM_HEAVY` once only the
  segmentation tools use it (they run on MONAI, not Procrastinate — decide and note their manifest).
- **Tests:** catalog validate; `test_every_heavy_tool_states_a_budget_it_can_finish_in`.

### E2. Phase 7.4 — modeling correctness (H1–H5, H8)
- **Files:** `modeling/results.py`, `classification.py`, `regression.py`, `training.py`, `inference.py`.
- **Do:** H1 model path digest + temp file + atomic rename; H2 train from captured bytes; H3
  `WorkspacePath`; H4 exact model match or error listing candidates; H5 no `os.chdir`; H8
  regressor gets the classifier's checks, progress, fold clamp, replay `chart_data`.

### E3. Phase 7.4 — radiomics correctness (H9–H11, H14–H18)
- **Files:** `radiomics/extract_features.py`, `extraction/`, `selection/`, `analyze_importance.py`.
- **Do:** H9 per-job params YAML; H10 one pool per job, real `n_workers`; H11 drop the CUDA probe;
  H14 per-job plots dir + digest key; H15 `mutual_info` `random_state`; H16 RFE
  `n_features_to_select=top_features`; H17 config digest in `set_key`; H18 validate in the input model.
  (H6 per-job log handler is D7; H13 per-job dirs here, the Node Runner half is Person 3's.)

### E4. Phase 7.4 — H12 in the Application API
- **Files:** `application_api/repositories/radiomics.py` (`:261`).
- **Do:** link reused rows to the current run unless `execution_policy.fresh`.
- **Tests:** live-DB test for reuse + fresh.

### E5. Phase 7.5 — progress + `on_result` hooks + job-run tests
- **Do:** all progress via the context sink (file sink inside a job, relayed by D11); radiomics
  `on_result` hook copied from `pipeline_monitor.py:182-252`.
- **Tests:** engine determinism/golden tests from Plan 7's list; source scan (no `pycaret`, no new
  `pycaret_*` names); `-m "integration and jobs"`: each of the 8 tools runs in `child.py` on a tiny
  synthetic CSV / NIfTI.
- **Done (Plan 7):** all 8 tools run as jobs on the scikit-learn engine.

---

## Stage F — Plan 16: other job systems

### F1. Phase 16.1 — S11 review long-poll
- **Files:** `application_api/.../segmentation_review.py` (`:682-705`).
- **Do:** key by batch, cancel the waiter, `request.is_disconnected()`. **Test:** waiter cleanup.

### F2. Phase 16.1 — S13 LaTeX compile
- **Files:** `application_api/repositories/reports.py` (`:298-304,346-354,433`), `toolpacks/reporting/latex_to_pdf.py`.
- **Do:** compile **before** the row lock with `asyncio.create_subprocess_exec` in its own process
  group, killed on timeout; client retries and resolves the 409 `stale_version`.
- **Test:** a fake slow compiler doesn't block the loop.

### F3. Phase 16.1 — S8 checkpoint PATCHes
- **Files:** `segmentation/batch_store.py` (`:228-243`). **Do:** PATCH only the changed pair.

### F4. Phase 16.2 — training as one server-side job (T1, T9)
- **Files:** `application_api/` training files (`routes/training_jobs.py`, repository, service).
- **Do:** prepare → create-model → train orchestrated by the API; `training_jobs` receipt committed
  **before** the POST to MONAI (D17, T9); reconcile 404 → resubmit same id; unfreeze only with no
  active receipt; frontend polling with backoff (T9). The train step goes through MONAI's
  **one GPU gate** (C2), preparation outside it; this replaces the browser's direct `/train/` (C5).
  Training is the one job not sent through `JobClient` (no node run).
- **Tests:** training job state machine.

### F5. Phase 16.2 — MONAI training fixes (T2–T8)
- **Files:** `monailabel/endpoints/training_jobs.py`, `create_model.py`, `Backend/apps/radiology/` trainers.
- **Do:** T2 declared fields, `extra="forbid"`; T3 use DB `dataset_id`; T4 freeze after accept;
  T5 pass 4xx through; T6/J5 preparation **out of** the GPU gate, the train step **inside** it — one
  gate shared with inference (C2), not a separate training pool (`training_jobs.py:118`);
  T7 granular cancel + partial-data rollback; T8 `try/finally` + `WeakValueDictionary` for `_workflow_locks`.

### F6. Phase 16.3 — ingestion as a job
- **Files:** `agent_system/ingestion/`, a new `harness`-visible job tool `ingest_convert`.
- **Do:** conversion on `compute.cpu` (fixes G2, G3, G6 by design); then G1 delete only what this
  attempt created, G4 prune as a periodic task, G5 webhook token header, G7 cancel/list endpoints +
  one frontend upload client, G8 shared `status` + `stage`.
- **Tests:** G1 failed rerun keeps the series; ingestion cancel / `INTERRUPTED`.

### F7. Phase 16.4 — Agent System tasks
- **Do:** K1 `best_effort.schedule_best_effort` for fire-and-forget tasks; K3 `reload` only when
  `APP_ENV=dev`. (K2 is Person 5's, Phase 12.4.)
- **Done (Plan 16):** every listed problem has a passing test.

---

## Stage G — Plan 17 (your part of the cut-over)

- Remove `INTERIM_HEAVY` leftovers, the old MONAI client code paths, `toolpacks/common/polling.py`
  re-export (if kept), the `/events` / `/ws-broadcast` producers you own (none expected).
- Docs: job platform, MONAI backend, compute worker in `cubeai-docs/` and `Backend/storage/README.md`.
- Run the Testing table rows for Jobs (unit + integration), Modeling engine, Tool packs, MONAI / segmentation.

---

## Checklist

- [x] A1 contract.py · [ ] A1b contract follow-up (reusable, monai.gpu, protocol_error) · [ ] A2 status + derive_status · [ ] A3 polling + budgets · [ ] A4 client + in-memory
- [ ] B1 modeling ports · [ ] B2 radiomics ports · [ ] B3 run_modeling params · [ ] B4 run_modeling handler · [ ] B5 modeling view hook
- [ ] C1 MONAI tests · [ ] C2 J1/J2/J11 + GPU gate · [ ] C3 J4/J6/J12 · [ ] C4 J8/J10/J3 · [ ] C5 J9 · [ ] C6 MonaiHttpBackend · [ ] C7 services.py + D10 ids · [ ] C8 heartbeat/threads/handle · [ ] C9 run_segmentation
- [ ] D1 Procrastinate app · [ ] D2 migration "s" · [ ] D3 dispatch/cancel · [ ] D4 read routes + SSE stream · [ ] D5 app + launcher · [ ] D6 supervisor · [ ] D7 child · [ ] D8 periodic + start-up · ~~D9 GPU pool~~ (dropped) · [ ] D10 runtime/job.py + router · [ ] D11 AppApiJobClient · [ ] D12 deploy · [ ] D13 integration suite
- [ ] E1 job manifests · [ ] E2 modeling H-fixes · [ ] E3 radiomics H-fixes · [ ] E4 H12 · [ ] E5 progress/hooks/job tests
- [ ] F1 S11 · [ ] F2 S13 · [ ] F3 S8 · [ ] F4 training job · [ ] F5 T2–T8 · [ ] F6 ingestion job · [ ] F7 K1/K3
- [ ] G cut-over items
