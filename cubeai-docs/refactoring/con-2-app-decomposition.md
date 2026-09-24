# CON-2 — `app.py` Decomposition: Change Report

**Task:** CON-2 from the [team execution plan](./execution-plan.md) (Phase 1 — Contracts & Foundations)
**Goal:** Split the Agent System monolith into domain routers **with no behaviour change**, so five
developers stop colliding in one file.
**Status:** Done and verified. On `Refactor-Version` it was re-derived during a cherry-pick of
`c4092e38`; the resolution is staged but **not committed** (see §0).
**Date:** 2026-09-24

---

> **Read §0 first.** CON-2 was originally built on commit `6d27900a` (§1–§7 describe that
> version). It was then cherry-picked onto `Refactor-Version`, whose Agent System had diverged
> substantially, so the decomposition was **re-derived from that branch's own `app.py`**. §0
> describes what is actually on `Refactor-Version`.

---

## 0. Applying CON-2 to `Refactor-Version` (cherry-pick conflict resolution)

### 0.1 What happened

Commit `c4092e38` ("fix app.py", the CON-2 change) was cherry-picked onto `Refactor-Version`
(`b016fff6`). Git stopped on two conflicts: `Backend/agent_system/app.py` and
`Backend/tests/pipeline/test_pipeline_orchestration.py`. It applied the 14 new `api/` files
"cleanly", but **they were broken on this branch**. Since `6d27900a`, `Refactor-Version` had:

- deleted `services/resource_scheduler.py`, `services/execution_capacity.py` and
  `services/resource_tools.py`;
- reverted "fix to allow concurrent pipelines" and "make websocket support multi sessions and
  prevent session diffusion", so `socket_store.py` and `session_service.py` were rewritten;
- added `services/mask_store.py`, `population_review.py`, `run_context.py`,
  `artifact_client.py`, an `ingestion/` router, and `storage_write_guard.py`;
- changed `app.py` by 676 insertions and 989 deletions, to 1,904 lines.

Neither side of the conflict was correct. Taking the CON-2 side would have erased the branch's
Orthanc, mask, population and ingestion work. Taking the branch side would have left 14 `api/`
files importing modules that no longer exist.

### 0.2 Resolution

The decomposition was **regenerated from `Refactor-Version`'s `app.py`** with the same design as
§2–§3:

- **Code bodies are line-range slices of the original.** A generator cut 17 ranges out of the
  branch's `app.py`, and an explicit coverage check confirmed that every one of its 1,904 lines is
  either moved into a module or deliberately kept in `app.py`, with no overlap. Only import headers
  were hand-written.
- **Renames applied to the slices:** `@app.<verb>` → `@router.<verb>`, `_review_events` →
  `review_events`, `_review_futures` → `review_futures`, `_domain_http_error` →
  `domain_http_error`, `_cors_origins` → `cors_origins`. A mechanical check found that every
  non-blank, non-comment line of the original appears verbatim in the new modules, except 22
  import statements that were redistributed.
- `app.py` is now **143 lines** (from 1,904). The `api/` package totals 1,929 lines.

**How the result differs from the §2 layout, because this branch differs:**

| Area | On `Refactor-Version` |
|---|---|
| `api/runtime.py` | `cors_origins`, `agents_workspace_dir`, `session_service`, `domain_http_error`. There is no resource scheduler, `WS_MAX_MESSAGE_BYTES` or internal-token check on this branch. |
| Import-time side effects | `ensure_storage_layout()` and `install_write_guard()` **stay in `app.py`**, after every import, exactly where the original ran them. |
| Router order | The ingestion router (`/ingest/*`) is included first, as in the original, then `files.router` → `/files` mount → the rest. |
| `api/review_state.py` | `review_events` (one `asyncio.Event` per session) and `review_futures`. Review results are database-backed here, so there are no disk helpers. |
| `routes/execution.py` | Includes the `/run/` trailing-slash alias (`include_in_schema=False`). |
| `routes/websocket.py` | Only `WS /ws`: there is no `/ws-status` and no session binding on this branch. |
| `routes/segmentation_review.py` | Also holds the Orthanc/`MaskStore` helpers (`_local_volume_path`, `_archive_edited_mask`, `_mask_from_orthanc`, …) and `REVIEW_RECHECK_SECONDS`. |
| `__main__` | Kept as the branch had it: `reload=True`, no WebSocket size or ping flags. |

**Other files touched by the resolution:**

| File | Change | Why |
|---|---|---|
| `tests/pipeline/test_pipeline_orchestration.py` | Took **this branch's** version (3 run-based tests) and retargeted `app` → `api.runners` | `run_pipeline` now lives in `api.runners`. Patching `app` would no longer reach it. |
| `tests/contracts/test_v1_artifact_contract.py`, `test_v1_execution_spine_contract.py`, `test_v1_population_review_contract.py` | The 4 source-text assertions that read `app.py` now read `_agent_http_source()`, i.e. `app.py` plus `api/**/*.py` | These tests assert that code is present in, **or absent from**, the HTTP layer. Against a 143-line `app.py`, 4 would fail, and 7 negative assertions (e.g. `"review_result.json" not in app`) would pass vacuously. Reading the whole layer keeps both kinds meaningful. `install_write_guard()` is still checked against `app.py` alone, where it genuinely lives. |
| `CLAUDE.md` | Corrected the bullet the cherry-pick added: `api/runtime.py` holds `domain_http_error`, not an internal-token check, and the storage guard stays in `app.py` | The cherry-picked text described the other base. |

### 0.3 Verification on `Refactor-Version`

The baseline was a pristine `git archive` of `b016fff6`, including `deploy/` and the frontend
sources that some contract tests read, with the same `deploy/.env` as the working copy.

| Check | Result |
|---|---|
| OpenAPI schema, route set (44), middleware, title/version | **Identical** |
| Routing resolution: original `app.py` loaded side by side, 70 probe URLs incl. `/run/`, `/ingest/*`, `/files` edge cases | **Identical** |
| Per-test outcomes, local venv: `unit` + `contracts` + `pipeline` + `regression/agent_system` (excl. `unit/radiology`) | **681 tests, identical** (680 pass, 1 fail on both sides: `test_a19_compile_raises_on_bad_latex` expects 422 but gets 503 because `pdflatex` is not installed locally) |
| Live smoke scenario (30 steps, separate processes, Application API deliberately unreachable) | **Identical** responses, including the error paths and `Cross-Origin-Resource-Policy` headers |
| Real uvicorn boot of `"app:app"` | Starts, runs the lifespan, serves `/client-config/providers` and `/ingest/formats` (200), 36 OpenAPI paths. Run on port 8199 because Docker Desktop holds 8150. |
| **Canonical Docker gate** `Backend/tests/run_tests.ps1` (`cubeai1/agents-app:latest`, already local) | **deterministic: 583 passed, 0 failed · agent-regression: 99 passed, 0 failed** |

### 0.4 Left for you

- **The cherry-pick is resolved and staged but not continued.** Nothing was committed. Review,
  then run `git cherry-pick --continue`.
- **`check_storage_boundary.ps1` fails in the main checkout** with 214 violations. All are
  per-workspace `cache/literature` files under
  `Backend/storage/hypotheses/workspace_*/cache/literature`, flagged "retired to shared
  storage/cache/literature". That is local runtime data (gitignored), not code, and unrelated to
  this change. It was left untouched.
- **Pre-existing `CLAUDE.md` text on this branch is stale.** The *Sessions, WebSocket, and
  scheduling* section and the `WAITING_FOR_RESOURCE` gotcha describe the session-bound WebSocket
  and resource scheduler that `Refactor-Version` reverted. This predates the cherry-pick and was
  not changed.

---

## 1. Result at a glance

| | Before | After |
|---|---:|---:|
| `Backend/agent_system/app.py` | 2,217 lines | 133 lines |
| Modules holding the HTTP layer | 1 | 13 (+ 2 empty `__init__.py`) |
| Registered routes (incl. docs, mount, WS) | 41 | 41 |
| OpenAPI schema | — | byte-identical |
| Deterministic suite (local) | 62 pass / 2 fail | 62 pass / 2 fail — same tests |
| Agent regression suite (local) | 98 pass (+22 subtests) / 1 fail | identical |

The three failing tests fail identically on the untouched code; they are pre-existing and unrelated
(see §6).

---

## 2. What was changed

### New package: `Backend/agent_system/api/`

```
Backend/agent_system/
├── app.py                       composition root only (133 lines)
└── api/
    ├── __init__.py
    ├── runtime.py               process-wide singletons + shared helpers
    ├── runners.py               run_planner / run_pipeline (the AutoGen GraphFlow boundary)
    ├── review_state.py          segmentation review-gate state (memory + disk)
    └── routes/
        ├── __init__.py
        ├── files.py
        ├── execution.py
        ├── websocket.py
        ├── events.py
        ├── client_settings.py
        ├── sessions.py
        ├── reports.py
        ├── populations.py
        └── segmentation_review.py
```

The `api/routes/` layout mirrors the existing `application_api/api/routes/` convention.

### Where each endpoint went

| Module | Endpoints | Suggested owner (per execution plan) |
|---|---|---|
| `routes/files.py` | `GET /files/{workspace_name}/{logical_path:path}`, `GET /static/{file_path:path}` | D3 |
| `app.py` | `Mount /files` (StaticFiles) | — |
| `routes/execution.py` | `POST /plan`, `/run`, `/pause`, `/resume`, `/session/{id}/resume-pipeline`, `/stop`; `GET /status/{id}` | D5 |
| `routes/websocket.py` | `WS /ws`; `GET /ws-status` | D2 |
| `routes/events.py` | `POST /events`, `POST /ws-broadcast` (internal ingress, blocked by OpenResty) | D2 |
| `routes/client_settings.py` | `GET`/`POST /client-config`, `POST /api/test-client-config`, `GET /client-config/providers` | D5 |
| `routes/sessions.py` | `GET /session/{id}/replay-logs`, `/config`, `/plan-state`, `/execution-state`, `/chat-messages`; `GET /hypothesis-history`; `POST /session/{id}/recover` | D1 |
| `routes/reports.py` | `POST /reports/compile` | D5 |
| `routes/populations.py` | `POST /populations/set-labelling-mode`, `GET /populations/cohort-state`, `POST /populations/update-cohort-list`, `/save-labels`, `/approve-cohort`, `/upload-targets` | D1 |
| `routes/segmentation_review.py` | `POST /segmentation-review/draft`, `POST /segmentation-review`, `POST /segmentation-review/upload-mask`, `GET /segmentation-review/mask/{id}/{uid}`, `GET /segmentation-review/wait/{id}` | D3 |

36 HTTP/WebSocket routes plus 1 mount = the original 37. With FastAPI's 4 docs routes, 41 in total.

### Where each piece of shared state went

| Was in `app.py` | Now in | Used by |
|---|---|---|
| `session_service = SessionService(...)` | `api/runtime.py` | runners + 6 routers; re-exported from `app` |
| `_publish_resource_status` + `resource_scheduler.set_notifier(...)` | `api/runtime.py` | set once at import |
| `_cors_origins` | `api/runtime.py` as `cors_origins` | CORS middleware + WS origin check |
| `ensure_storage_layout()` + `agents_workspace_dir` | `api/runtime.py` | `/files` mount + `/static` |
| `WS_MAX_MESSAGE_BYTES`, `_positive_int_env` | `api/runtime.py` | `/ws` + `uvicorn.run` |
| `_require_internal_event_token` | `api/runtime.py` as `require_internal_event_token` | `/events`, `/ws-broadcast` |
| `_review_futures`, `_review_events` + 5 disk helpers | `api/review_state.py` (leading `_` dropped — they are now a shared module API) | `execution`, `sessions`, `segmentation_review` |
| `run_planner`, `run_pipeline`, `api_key` | `api/runners.py` | `execution`, `sessions` |
| `RedactApiKeyFilter`, logger levels, `lifespan`, `CoopCoepMiddleware`, `FastAPI(...)`, `__main__` | stay in `app.py` | — |

### Other edited files

| File | Change | Why |
|---|---|---|
| `Backend/tests/pipeline/test_pipeline_orchestration.py` | `import app as app_module` → `import api.runners as runners` (8 references) | The test monkeypatches `GraphFlow`, `PipelineMonitor` and `create_model_client_from_config` on the module that *defines* `run_pipeline`. Patching `app` would no longer reach it, and the test would silently start exercising real AutoGen. |
| `CLAUDE.md` | One bullet under *Conventions → Backend* describing the new layout and its two ordering/re-export constraints | Keeps future sessions from adding routes to `app.py` |
| `cubeai-docs/refactoring/execution-plan.md` | CON-2 marked `DONE`; the A1 row now points at `api/runners.py` | The plan referenced `app.py:319` |

**Code was moved verbatim.** Apart from `@app.<verb>` → `@router.<verb>` decorators, the only edits
inside moved code are renamed references to the shared helpers listed above (e.g. `_review_events` →
`review_events`). This was checked mechanically: after applying exactly those renames, every
non-blank, non-comment line of the original `app.py` appears verbatim in the new modules, except 19
lines — all of them import statements, which were redistributed to the modules that need them.

---

## 3. Design decisions

1. **Route order is preserved where it matters, and only there.** Starlette matches in
   registration order, and the `/files` StaticFiles mount matches *everything* under `/files/`.
   `files.router` is therefore included **before** the mount, exactly as in the original. All other
   routes have non-overlapping paths, so they are grouped by domain. This was verified with 66 probe
   URLs (§5.2) rather than assumed.
2. **`session_service` stays importable from `app`.** `tool_logging.py` and
   `segmentation/pipelines/common.py` do `from app import session_service` lazily, and
   `test_task_cancellation.py` injects a fake `app` module into `sys.modules` to test those paths.
   Pointing those files at `api.runtime` would silently bypass that test, so they were left alone.
3. **Mounts cannot live in an `APIRouter`.** FastAPI's `include_router` drops `Mount` routes, so
   the `/files` mount stays in `app.py`.
4. **`from groq import BaseModel` was kept** for the `latex_output` model in `routes/events.py`.
   It is the groq SDK's pydantic subclass, not `pydantic.BaseModel`. It looks accidental, but
   `/events` validates a `ToolEvent | latex_output` union, and switching base classes could change
   validation, so it is flagged in a code comment rather than "fixed" here.
5. **`uvicorn.run("app:app", ...)` is unchanged**, so both entry points keep working:
   `CMD python ./Backend/agent_system/app.py` in `deploy/Dockerfile.agents`, and uvicorn's reload mode.

---

## 4. Intentional, non-functional differences

None of these changes a request, response, status code, header, WebSocket frame or persisted file.

| Difference | Effect |
|---|---|
| **Logger names** for moved code change from `app` to the module path, e.g. `api.routes.populations`. Explicit names (`HypothesisFlow`, `Execution Pipeline`) are unchanged. | Visible in log lines only. Nothing in the repo filters by the `app` logger name (checked). |
| **`RedactApiKeyFilter` is installed before the local imports** instead of after them. | Strictly broader: API-key redaction now also applies during import-time logging. |
| **One `SessionService` per process** under `python app.py`, instead of two. The old file ran its top level twice (as `__main__` and again as `app` when uvicorn imported it); only the `app` copy ever served requests. | Removes an unused duplicate; the served instance is unchanged. |
| **Route list order differs** for non-overlapping routes (e.g. `/ws-status` now sits with `/ws`). | None: resolution is identical for every probe (§5.2), and the OpenAPI JSON is identical. |
| `app` module no longer exposes `run_pipeline`, `run_planner`, `GraphFlow`, `BasicPipelineBuilder`, `PipelineMonitor`, `TaskResult`, `create_model_client_from_config`. | Only the pipeline test used them, and it now imports `api.runners`. |
| `import re` in `RedactApiKeyFilter` moved from inside `filter()` to module top. | None. |

---

## 5. Verification

### 5.1 Environment

Docker Desktop was not running, so the canonical `Backend/tests/run_tests.ps1 -Runtime docker` path
could **not** be used. Verification used the project venv
(`C:\Amira\Graduation Project\OHIF-Standalone\venv`, Python 3.11.3, with AutoGen installed), with
two caveats:

- The venv lacks `highdicom`, `pydicom` and `pynrrd`. A test-only `sitecustomize.py` shim, kept in
  the session scratchpad and never added to the repo, **appended** the global Python 3.11
  site-packages *after* the venv's, so those pure-Python packages resolved while the venv's pinned
  `numpy 1.26.4` kept precedence. Nothing was installed and the venv was not modified.
- `Backend/tests/unit/radiology/` was excluded from both runs because it needs PyTorch/MONAI, which
  are not installed locally. It does not import `agent_system`.

Every comparison ran the **same command** on the untouched code (a `git archive HEAD` copy) and on
the refactored tree.

### 5.2 HTTP surface

| Check | Result |
|---|---|
| Full OpenAPI schema (paths, operationIds, request/response models, components) | **Identical** |
| Route set (class, path, name, methods, endpoint function name) | **Identical** (41) |
| Middleware stack | **Identical** (`CoopCoepMiddleware` → `CORSMiddleware`) |
| App title / version / description | **Identical** |
| Resource-scheduler notifier wired | **Yes** in both |
| `legacy_workspace_file` registered before the `/files` mount | **Yes** |
| Routing resolution: the original `app.py` loaded from git alongside the new one, 66 probe URLs (one per route + method, the WS route, plus edge cases like `/files/x.pdf`, `/files/notaworkspace/x`, `/segmentation-review`, `/unknown`), first full match compared | **Identical for all 66** |

### 5.3 Tests

| Suite | Before (HEAD) | After | Same tests? |
|---|---|---|---|
| Deterministic: `unit` + `contracts` + `pipeline` (excl. `unit/radiology`) | 62 passed, 2 failed | 62 passed, 2 failed | yes |
| Agent regression: `regression/agent_system` | 98 passed, 22 subtests passed, 1 failed | identical | yes |
| Combined per-test outcome list (171 lines, sorted) | — | — | **Identical line for line** |
| `test_run_pipeline_completes_mocked_full_agent_flow` | passed | passed (against `api.runners`) | yes |

### 5.4 Live behaviour

**Smoke scenario, 36 steps, run in separate processes against both apps. Responses identical.**
Coverage included:

- success and validation paths for `/ws-status`, `/client-config/providers`, `/status`, `/pause`, `/plan`;
- `/files` guards (non-workspace, bad category, missing artifact) and the `/static` traversal guard;
- the population flow across routers: `set-labelling-mode` → `/session/{id}/config` →
  `cohort-state` → `save-labels` → `approve-cohort`;
- **the cross-module review gate:** `POST /segmentation-review` stores the result, then
  `GET /segmentation-review/wait/{id}` returns it, then a second wait returns `408` once consumed;
- `/segmentation-review/draft` and `/mask` error paths, and `/reports/compile` validation;
- `/events` union validation (`422`), plus `/ws-broadcast` with the internal token → `403` without
  it, `200` with it;
- WebSocket: missing `session_id` → close `1008`; `client_message_ack` (new and duplicate);
  `protocol_error` for `session_id_mismatch` and an invalid `message_id`; planner input-queue depth;
- `Cross-Origin-Resource-Policy` and `Access-Control-Allow-Origin` headers on every response.

**Real server boot:** `python ./Backend/agent_system/app.py` (the Dockerfile command) printed
"Agents API worker startup complete", served `GET /ws-status` → `200` within about 4 s, and its
`/openapi.json` listed 34 paths. The server was then stopped.

**Repo hygiene:** `Backend/scripts/check_storage_boundary.ps1` reports *clean*. All runs used
temporary storage roots, so nothing was written under `Backend/storage/`. `git status` shows only
the intended changes. The index normalises to LF (`core.autocrlf=true`), so there are no
line-ending changes in the diff.

---

## 6. Pre-existing failures (not caused by this change)

All three fail identically on the untouched HEAD code.

| Test | Cause |
|---|---|
| `unit/agents/test_feature_extraction_agent.py::test_create_selects_exact_feature_tool_contract` | `services/resource_tools.py:23` calls `delegate.args_type()`, but the test's fake tools are `SimpleNamespace` objects without it. The test predates resource-guarded tools. |
| `unit/agents/test_modeling_agent.py::test_create_selects_exact_modeling_tool_contract` | Same cause. |
| `regression/agent_system/test_empty_pipeline_guards.py::…::test_unhandled_planner_failure_is_broadcast` | `session_service` now emits a `state_update` event before `msg`/`planning_failed`; the test's expected list predates that. |

These may also fail in CI (not checked, because Docker was unavailable). They should be triaged
separately.

---

## 7. Not done / follow-ups

- **Not committed or pushed.** Review the diff first; commit when ready.
- **Docker-based run still outstanding.** Run the canonical gate once Docker Desktop is up:
  `powershell -NoProfile -ExecutionPolicy Bypass -File Backend/tests/run_tests.ps1 -Suite deterministic`
  and `-Suite agent-regression`.
- **Behaviour-preserving means warts were preserved too.** The moved code keeps its existing issues:
  the `groq` `BaseModel` import, `print()` debug output in `upload_targets`, bare `except:` in
  `get_plan_state`, unused `workspace_folder` locals, a redundant `import json` inside
  `upload_modified_mask`, and `json.load(open(state_file))` without a context manager in
  `run_pipeline`. Each is now a small, isolated change in a single owner's module.
- **`from app import session_service`** in `tool_logging.py` and `segmentation/pipelines/common.py`
  could later point at `api.runtime`, together with an update to `test_task_cancellation.py`, which
  currently patches `sys.modules["app"]`.
- `OPTIONAL SUGGESTION`: turn the route-surface comparison (OpenAPI + route set + resolution
  probes) into a contract test under `Backend/tests/contracts/`, so later refactors (e.g. RW-2,
  AG-1) get the same guarantee automatically.
