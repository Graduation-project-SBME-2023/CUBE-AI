# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CUBE.AI — a medical-imaging research platform. A customized OHIF Viewer (v3.8.0-beta.8)
front end drives four backend services: an Application API (owns PostgreSQL), an LLM
multi-agent research pipeline, MONAI Label (segmentation/training), and Orthanc (PACS).
The flagship feature is the **Research Playground**: a user states a clinical research
question, and an agent pipeline builds a cohort, segments, extracts radiomics features,
trains models, and generates a PDF report.

`cubeai-docs/` is the human-facing documentation tree; start from `cubeai-docs/system-overview.md`.
Parts of it are stale — see **Gotchas**.

## Tech Stack

- **Backend:** Python 3.13 in the images we build (`python:3.13.16-slim` for agents,
  `python:3.13-slim` for the Application API; the MONAI Label image keeps its own 3.10), FastAPI, async
  SQLAlchemy + asyncpg, Alembic, Pydantic v2 (`pydantic-settings`), AutoGen
  (`autogen_agentchat` `GraphFlow`/`DiGraphBuilder`; the legacy pipeline, frozen until its
  removal in Plan 17), PyRadiomics, scikit-learn / LightGBM / XGBoost / SHAP (PyCaret is
  removed; the modeling tools run on the scikit-learn engine in `agent_system/toolpacks/modeling/engine/`), MONAI Label / nnUNetv2 / VISTA3D / SAM2 / VoxTell,
  PyTorch. The framework-neutral tool platform (`agent_system/toolkit/` and
  `agent_system/toolpacks/`, see **Tool packs**) is written for **Python 3.13**.
- **Frontend:** OHIF Viewer monorepo — React **17**, TypeScript 4.6, **Webpack 5** (not Vite),
  Lerna + Yarn 1 workspaces (`platform/*`, `extensions/*`, `modes/*`), Jest, ReactFlow
  (`@xyflow/react`), Recharts, Cornerstone3D.
- **Data:** PostgreSQL 18 (`workflow_db`), Orthanc (DICOM + DICOMweb).
- **LLM:** Ollama by default (`OLLAMA_MODEL`, default `gpt-oss:120b-cloud`); OpenAI /
  Azure OpenAI also supported via `Backend/agent_system/client_config.py`.
- **Deploy:** Docker Compose (`deploy/`), OpenResty ingress, PowerShell/bash launchers in `scripts/`.

## Commands

### Run the stack

```powershell
powershell -ExecutionPolicy Bypass -File scripts/launch.ps1          # Windows, full stack
powershell -ExecutionPolicy Bypass -File scripts/launch.backend.ps1  # backend only
```

```bash
bash scripts/launcher.linux.sh          # Linux/macOS, full stack
bash scripts/launcher.backend.linux.sh  # backend only
```

**The stack is GPU-only** — the CPU backend variant was removed, and
`deploy/docker-compose.backend.cpu.yml` no longer exists. Every launcher hardcodes
`docker-compose.backend.gpu.yml`. The only remaining CPU image is
`cubeai1/custom-monailabel:v1.0-cpu`, used by CI and the `radiology` test suite.

The "full stack" launchers start the backend plus the **production** frontend
(`docker-compose.frontend.prod.yml`, OpenResty on `:80`) — not the webpack dev server. For
frontend work, run the backend-only launcher and `yarn dev` separately (see **Frontend**).

Manual dev startup (`COMPOSE_PROJECT_DIR` **must** be the absolute repo root):

```bash
export COMPOSE_PROJECT_DIR="$(pwd)"   # PowerShell: $env:COMPOSE_PROJECT_DIR = (Get-Location).Path
docker compose --env-file deploy/.env -f deploy/docker-compose.backend.gpu.yml -f deploy/docker-compose.backend.dev.yml up
```

Overlays: `backend.dev` (bind-mounts `application_api` + `storage` for hot reload; the base
file deliberately does not, so production migrates with the image's baked code),
`backend.prod` (`ports: !reset []` — OpenResty becomes the only published ingress),
`frontend.prod` (OpenResty + built OHIF). `docker-compose.frontend.dev.yml` exists but is
marked unused in its own header comment ("react app runs super slow on docker").

### Backend tests

`Backend/tests/run_tests.ps1` is the canonical runner; `-Runtime docker` (default) uses the
project images, so no local Python is needed.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File Backend/tests/run_tests.ps1 -Suite deterministic
```

Suites: `deterministic` (unit + contract + pipeline, minus `legacy_autogen` — **the PR gate**),
`legacy-autogen` (the AutoGen-pipeline tests, auto-marked by `LEGACY_AUTOGEN_TESTS` in
`Backend/conftest.py`), `agent-regression`,
`radiology`, `integration` (needs running services), `all`, `list`. Flags: `-Coverage`,
`-Runtime local -Python <exe>`. JUnit goes to `Backend/test-results/`, coverage to
`Backend/coverage.xml` and `Backend/htmlcov/`.

Direct pytest (always pass `-c Backend/pytest.ini`; markers are strict):

```bash
python -m pytest -c Backend/pytest.ini Backend/tests/unit Backend/tests/contracts Backend/tests/pipeline -m "(unit or contract or pipeline) and not legacy_autogen"
```

```bash
python -m pytest -c Backend/pytest.ini Backend/tests/unit/agents/test_modeling_agent.py
```

```bash
python -m pytest -c Backend/pytest.ini Backend/tests/unit -k "test_name"
```

Test deps: `python -m pip install -r Backend/requirements/test.txt` (plus
`Backend/agent_system/requirements.txt` for the runtime).

**The toolkit and the tool packs need Python 3.13** (PEP 695 syntax). The images and CI run
3.13, so the deterministic suite collects them. A local interpreter older than 3.13 cannot
import `tests/unit/toolkit/`, `tests/unit/toolpacks/`, or the tests that import
`agent_system.toolpacks` (`tests/unit/functions/`, `tests/contracts/test_agent_output_contract.py`);
`PY313_ONLY_TESTS` in `Backend/conftest.py` leaves them out of collection there (the report
header says so). Without the full runtime, a 3.13 interpreter with the test deps plus
`pydantic httpx numpy pandas scikit-learn requests_toolbelt pyyaml aiofiles fastapi greenlet
pydicom` runs them (`py -3.13` instead of `python3.13` on Windows):

```bash
python3.13 -m pytest -c Backend/pytest.ini Backend/tests/unit/toolkit Backend/tests/unit/toolpacks Backend/tests/unit/functions Backend/tests/contracts
```

### Frontend

Toolchain: `engines` asks for Node `>=16` / Yarn `>=1.16`, and `Dockerfile.frontend.dev` pins
`node:18-slim`, but the monorepo builds cleanly on current Node (verified on **Node 24.19.0 +
Yarn 1.22.22**). Yarn **1** is required — this is a Yarn-1-workspaces + Lerna repo, so do not
let corepack activate Yarn 3/4. On Windows, `corepack enable` fails with `EPERM` when Node is
installed under `C:\Program Files\nodejs`; `npm install -g yarn@1.22.22` (user-level npm
prefix) works without elevation.

```bash
cd Frontend/OHIF-Viewer && yarn install
```

```bash
yarn dev              # webpack dev server on :3000
```

`yarn dev` -> `lerna run dev:viewer` -> `webpack serve --config .webpack/webpack.pwa.js` with
the default `APP_CONFIG`; it proxies `/pacs` to `http://localhost:8043/` (the dev Orthanc), so
it expects the backend-only launcher to be running. A cold bundle takes ~70s and the dev-mode
`app.js` is ~35 MiB, so the first paint lags the "compiled successfully" line. `Failed to load
./.env.` in the output is benign — there is no frontend-local `.env`. Other `dev:*` variants
(`dev:orthanc`, `dev:dcm4chee`, `dev:static`) just swap `APP_CONFIG`.

```bash
yarn build            # production bundle -> platform/app/dist/
```

```bash
yarn test:unit        # jest --collectCoverage
```

```bash
yarn test:unit:ci     # lerna, per-package, CI mode
```

Lint/format are ESLint + Prettier via the OHIF configs (`.eslintrc.json`, `.prettierrc`);
`lint-staged` + husky run Prettier on commit. There is no repo-wide `yarn lint` script.

### Other checks

```powershell
powershell -ExecutionPolicy Bypass -File Backend/scripts/check_storage_boundary.ps1
```

CI-enforced and also wired as a pre-commit hook. Read-only whole-system load smoke test
(requires running services):

```powershell
powershell -ExecutionPolicy Bypass -File Backend/tests/load/run.ps1 -Profile smoke
```

Profiles: `smoke` (default), `load`, `stress`.

### Database

```bash
cd Backend && alembic -c application_api/alembic.ini upgrade head
```

```bash
cd Backend && alembic -c application_api/alembic.ini revision --autogenerate -m "message"
```

There are no separate migrate/seed services: the `application-api` image's CMD runs
`alembic upgrade head`, then `python -m application_api.seeders`, then the server, chained
with `&&` and before uvicorn forks its workers (`deploy/Dockerfile.application-api`). A new
migration is therefore applied by `docker compose restart application-api`; a migration that
fails keeps the API from serving, and the restart policy retries it.

## Architecture Notes

### Service ownership (these boundaries are enforced by tests — do not blur them)

| Service         | Source                                           |     Dev port | Prod prefix              |
| --------------- | ------------------------------------------------ | -----------: | ------------------------ |
| OHIF Viewer     | `Frontend/OHIF-Viewer/`                          |         3000 | `/` (OpenResty :80/:443) |
| Application API | `Backend/application_api/`                       |         8001 | `/api`                   |
| Agent System    | `Backend/agent_system/`                          |         8150 | `/agents`, `/ws`         |
| MONAI Label     | `Backend/monailabel/`, `Backend/apps/radiology/` |         8000 | `/inference`             |
| Orthanc         | `Backend/orthanc/` + image                       | 8043 to 8042 | `/pacs`                  |
| PostgreSQL      | image                                            | 5433 to 5432 | none                     |

- **Only the Application API gets `DATABASE_URL`.** All ORM models, repositories, schemas,
  seeders and Alembic migrations live under `Backend/application_api/`. MONAI Label must
  never gain DB access; workflow training starts at the Application API, which delegates
  the long job to MONAI and returns a job id to poll.
- OpenResty **strips** the `/api`, `/agents`, `/inference`, `/pacs` prefixes, so FastAPI
  routers are defined without them.
- The Agent System calls other services over HTTP (`APP_API_URL`, `INFERENCE_API_URL`); it
  does not touch PostgreSQL directly.
- Orthanc's Lua stable-series hook calls `POST /sync/orthanc-webhook` on the Application API,
  which upserts patient/study/volume metadata.
- **Schema vocabulary (Phase 6.1, Appendix P of the roadmap).** Cut-over 1
  (`o1f0a2c3d4e5_v1_o_renames`) renamed `plans` → `session_graphs`, `plan_steps` →
  `graph_nodes`, `agent_runs` → `node_runs`, `ws_log` → `session_events`, `artifacts` →
  `stored_files`, `segmentation_runs` → `segmentation_batches`, `segmentations` →
  `segmentation_masks` and `models` → `inference_models`, with their columns (`run_id`,
  `node_id`, `created_by_run_id`, `file_id`, `inference_model_id`, `sessions.control_state`,
  ...) and routes (`/runs`, `/session-graphs`, `/graph-nodes`, `/session-events`, `/files`,
  `/segmentation-batches`, `/segmentation-masks`, `/inference-models`, `/chat-messages`).
  `Backend/tests/contracts/test_retired_vocabulary.py` fails the deterministic suite on an old
  name in production source; old names live only in migrations, the replayed legacy event type
  `pipeline_agent_status`, and tool contracts that keep their result keys.
- **Session graph and harness transactions (Phases 6.2–6.5).** Cut-over 2
  (`p1f0a2c3d4e5_v1_p_session_graph`) adds `users`, `graph_changes`, `run_attempts`
  and `training_jobs`. Nodes change **only** through graph changes
  (`POST /session-graphs/session/{id}/changes`, T1/T2); runs are created **only** by
  `POST /runs/start` (T4) with their first attempt; the old plan draft and approve routes are removed.
  Every write to a fenced table must declare its D24 path: a write route on the execution path
  takes `Depends(fenced_db)` (the caller's `X-Attempt-Id` / `X-Run-Id`), a harness or gate
  transaction opens `fenced_transaction(db, HARNESS | USER)` (`services/fencing.py`) — the
  database refuses anything else, and `tests/contracts/test_v1_harness_contract.py` scans for
  it. The pure T1/T2 and execution rules are in `application_api/core/graph_content.py` and
  `core/execution_rules.py`. Agent-side: `clients/harness_client.py` and
  `execution/graph_store.py` (+ `InMemoryGraphStore`, held to the same scenario table as the
  live API). Live database suites: `Backend/tests/run_live_db_tests.ps1`.
- **Agent transcripts and user approvals (Phase 6.6).** Cut-over 3
  (`t1f0a2c3d4e5_v1_t_agent_transcripts`) drops the LangGraph checkpoint tables (their routes and
  `/gate-decision` are removed) and adds `agent_transcript_entries` (an agent node's Claude
  Agent SDK session per run, fenced: only the run's latest running or suspended attempt appends)
  and `main_transcript_entries` (the Main's session). A suspension is v2 (`ask_user |
  tool_approval | pause` with `tool_use_id`), answered by `POST /runs/{id}/attempts/{id}/answer`
  (a domain form by its own route); `run_attempts.pending_tools` drives retry rule (f). A node's
  `approval: before | after` (D28) yields `NEEDS_RELEASE` and the `user_review` hold, and only a
  change the user authored releases either (`USER_RELEASE_REQUIRED`, also enforced by a trigger).
  Without an approval asked for, the cohort is frozen `auto` and a batch `auto_accepted`.

### Generated-data boundary

**All** generated backend data lives under `Backend/storage/` and must be resolved through
`Backend/storage_paths.py` (`workspace()`, `workspace_subpath()`, `ws_sub()`,
`ensure_storage_layout()`). Never assemble runtime paths ad hoc, and never write runtime
artifacts (`*.nii.gz`, `*.pt`, `*.gif`, `workspace_*`, `execution_*.log`, ...) next to source —
`check_storage_boundary.ps1` fails CI and blocks commits if you do.

That static check now has a runtime counterpart: `Backend/storage_write_guard.py`
(`install_write_guard()`, `check_write()`) refuses writes to workspace paths whose data was
moved into PostgreSQL by the V1 migration — `state/scratchpad.json`, `state/state.json`,
`outputs/agents/*_output.json`, `state/config.json`, `data/populations/*`,
`outputs/features/*` and more, each listed in `RETIRED_WORKSPACE_PATHS` with the table that
replaced it. If a write raises here, the fix is to write the DB row, not to pick a new filename.
The guard is mounted into the agents container by the compose file, so it applies in dev too.

Session data lives at
`hypotheses/workspace_<session-id>/{state,data,outputs}`; `cache/` and `temp/` are the only
disposable trees. `Backend/storage/README.md` documents the layout and the env overrides.

### Agent pipeline (`Backend/agent_system/`)

Planning (`agents/planning_agents/`) turns the question into a hypothesis plus an ordered
plan, optionally enriched by `agents/literature_review_agent/`. Execution
(`teams/execution_team.py`) builds an AutoGen `GraphFlow` from `AGENT_FACTORIES`, which maps
canonical agent ids to factories, in this order:

`study_population_planner_agent` -> `segmentation_agent` -> `feature_extraction_agent` ->
`modeling_agent` -> `result_reporter_agent` -> `latex_reporter_agent`

**Adding an agent = adding an entry to `AGENT_FACTORIES` plus its factory**; the dynamic
builder picks it up. Agent ids must stay identical across team wiring, scratchpad
reads/writes, planner output and `write_agent_output()` payloads.
Per-agent I/O contracts are in `cubeai-docs/agent-system/io-contracts/`.

**The tools moved out of `Backend/mcp/` (deleted in Plan 2).** They live in
`agent_system/toolpacks/<pack>/`, registered with `@tool` and found by the catalog
(`toolkit/catalog.py`), and run in the Agent System process through
`toolkit/runtime/dispatch.py` — see **Tool packs** under Conventions. The AutoGen agents above
still point their MCP workbench at the deleted `Backend/mcp/server.py`, so **the legacy
pipeline cannot run its tools**; it stays frozen until the cut-over (Plan 17). The same holds
for `SegmentationModule.initialize()` until Phase 9.7.

Agent-to-agent state is now **database-backed, not file-backed**. The `scratchpad_*` tools
(`agent_system/toolpacks/bookkeeping/scratchpad.py`) read and write `node_runs` rows — `scratchpad_notes`,
`scratchpad_warnings`, `scratchpad_summary` — keyed by the run, not the agent name. `state/scratchpad.json`,
`state/state.json` and `outputs/agents/*_output.json` are **retired**: writing them raises at
runtime (see the write guard below). Do not reintroduce a workspace file for state that now
has a table.

### Sessions, WebSocket, and scheduling

Everything is scoped to a `session_id`. The WebSocket is **push-only** and **session-bound at
the handshake** (`Backend/agent_system/api/routes/websocket.py`,
`Backend/agent_system/socket_store.py`) — see `WEBSOCKET_FIX_PLAN.md` if that file is present
in your checkout; it documents the design below ticket by ticket.

- `/ws?session_id=<uuid>&after_event_id=<n>` — `session_id` is required and must be a UUID;
  anything else closes with **1008** before any frame is sent. `after_event_id` is best-effort
  (missing/malformed defaults to `0`, i.e. full replay — never a rejection). The server keeps
  `dict[session_id, set[Connection]]`: a socket only ever receives its own session's frames, and
  each `Connection` has its own bounded outbound queue and sender task
  (`WS_OUTBOUND_QUEUE_MAXSIZE`, default 200; `WS_SEND_TIMEOUT_SECONDS`, default 3s) — one stalled
  tab is evicted (close code 1013) on overflow, not allowed to delay anything else.
- **Catch-up is part of the handshake, not a separate HTTP call.** On connect, the server pages
  the durable log from `after_event_id` (500 rows at a time) as `replaying: true` frames, sends a
  `replay_complete` frame, then drains live traffic — with a per-connection watermark so an event
  committed mid-replay is delivered exactly once. A durable-log read failure sends a
  `replay_failed` frame and closes (1011) rather than looking like an empty session.
- **Every event type is registered as durable or ephemeral**
  (`Backend/agent_system/services/event_registry.py`). Only durable events are persisted and get
  a real `event_id`; ephemeral events (e.g. `tool_progress`) are fanned out live and never
  replayed. `classify()` never raises — an unregistered type logs an error and defaults to
  durable — but a static source scan (`Backend/tests/unit/services/test_event_registry.py`) fails
  the deterministic suite if a new emitted type isn't in the registry.
- **Envelope v2:** `{v, event_id, session_id, node_id, run_id, attempt_id, type, payload,
  replaying, ts}`. `event_id` is `null` for ephemeral events; `node_id`/`run_id`/`attempt_id`
  are nullable and populated where the emitter already knows them (Phase 6.1 renamed the v1
  `run_id`/`step_id`/`agent_run_id` fields).
- **The client sends nothing over the socket.** User chat, prompt input, and any other
  client-originated text goes over REST — `POST /session/{id}/input` with an `Idempotency-Key`
  header, which feeds the same per-session queue the planner blocks on
  (`ws_manager.wait_for_user_message`). `/ws`'s receive loop only pumps the ASGI connection to
  detect a client-initiated close; anything a client still sends is logged and dropped. Pipeline
  start/pause/resume/stop (`/plan`, `/run`, `/pause`, `/resume`, `/stop`) remain REST, as before.
- **In-process emitters never call the agent system's own HTTP port.** Code running inside the
  agent-system process (the segmentation module, the view emitter, the planner) calls
  `socket_store.manager` directly, and a tool reports through its `ToolContext`'s event sink
  (`toolpacks/common/emit.py`: `emit`, `emit_progress`, `emit_view`; the sink is
  `toolkit/runtime/session_sink.py`). The internal routes `/events` and `/ws-broadcast` have no
  producer in the repository any more (the MCP subprocesses were their only one); they stay,
  authenticated with `WS_INTERNAL_TOKEN` (`X-Internal-Token` header, `hmac.compare_digest`),
  until Plan 17.3 — and the Agent System still refuses to start with the token unset unless
  `WS_INTERNAL_TOKEN_DEV_BYPASS=1` is also set.
- **There is exactly one WebSocket client**: `extensions/research-playground/src/utils/WebsocketService.ts`.
  The old `platform/app/src/utils/WebsocketService.ts` copy and the dead `<ChatWidget />` that
  imported it are deleted. The client opens one socket per session, sends its stored replay
  cursor as `after_event_id` on every connect, drops any frame with a missing or foreign
  `session_id` as defence in depth, dedupes by `event_id` per session, and ends hydration on the
  `replay_complete` frame rather than racing a separate fetch. Reconnect backs off exponentially
  with full jitter (base 1s, capped at 30s) instead of a fixed interval.

There is no `PIPELINE_MAX_CONCURRENCY` or GPU-lease system gating pipeline execution, and no
`WAITING_FOR_RESOURCE` status — neither exists in this codebase; if you find either mentioned
elsewhere (docs, an old PR, a teammate's memory of the design), it does not describe the code as
it stands. `Backend/agent_system/ingestion/limits.py` has its own semaphore-based limiter
(`cpu_heavy`, `orthanc_io`) for ingestion work specifically — unrelated to pipeline execution or
to the WebSocket layer.

Socket routing (`session_connections`, per-connection queues) and session/execution state
(`SessionStore`) are process-local, in-memory structures with no shared backing store: **run
exactly one Agent System worker.** Multi-worker needs a shared broker/store first.

## Conventions

### Backend

- Strict layering in `application_api/`: `api/routes/*` (thin, `APIRouter(prefix=..., tags=[...])`,
  `HTTPException` for client-facing errors) -> `repositories/*` (all SQLAlchemy; returns `None`
  or `(success, error_message)` for not-found/failure rather than raising) -> `db/models/*`
  (SQLAlchemy) and `schemas/*` (Pydantic, `model_config = ConfigDict(from_attributes=True)`).
  A new entity normally means one file in each of those four directories plus a router
  registration in `application_api/main.py`.
- Repositories `await db.commit()` inside `try`, and `await db.rollback()` on `SQLAlchemyError`.
- `async def` everywhere on the request path; async SQLAlchemy sessions via `Depends(get_db)`.
- Config through `pydantic_settings.BaseSettings` (`application_api/core/config.py`) reading
  `deploy/.env`; tunables elsewhere read `os.environ` with a safe fallback (see
  `client_config.py` `_retry_delays()`).
- `logger = logging.getLogger(__name__)` per module.
- Agent System HTTP layer: `agent_system/app.py` is only the composition root (middleware,
  lifespan, router registration, `__main__`). Endpoints live in `agent_system/api/routes/<domain>.py`;
  shared singletons (`session_service`, CORS origins, `domain_http_error`) in `api/runtime.py`;
  `run_planner`/`run_pipeline` in `api/runners.py`; review-gate state in `api/review_state.py`.
  `ensure_storage_layout()` and `install_write_guard()` stay in `app.py`, after every import.
  A new router must be added to the `include_router` loop in `app.py`. Keep `files.router` ahead
  of the `/files` StaticFiles mount, and keep `session_service` re-exported from `app`
  (`tool_logging.py` and `segmentation/pipelines/common.py` import it from there).
- Model, schema, repository and route files are named after their table (`node_runs.py`,
  `session_events.py`, ...). A few older files are not (`patient.py`, `volume.py`) — match the
  existing file you are editing rather than renaming it.

### Tool packs (`agent_system/toolpacks/`)

One package per pack (`bookkeeping`, `db_reads`, `literature`, `modeling`, `population`,
`radiomics`, `reporting`, `segmentation`); `common/` holds the shared helpers and registers no
tools. The catalog walks the packages, so a new module is picked up with no import list. Every
tool meets the **Definition of Ready** (`plan.md`, end of the roadmap), and
`tests/unit/toolpacks/light/test_definition_of_ready.py` enforces it for every light tool.

- **Layout:** a tool module holds the `@tool` function and its input model; a multi-step tool
  keeps its steps in a subpackage of plain, synchronous functions (`radiomics/extraction/`,
  `radiomics/selection/`, `modeling/engine/`, `population/anatomy.py`,
  `segmentation/monai_client.py`) and only orchestrates them. CPU work and file I/O run off the
  event loop (`asyncio.to_thread`); PyRadiomics runs in `extraction/worker.ExtractorPool`.
- **Shared helpers (`common/`):** `values.py` (`json_safe`, `finite_float`, `str_list` /
  `int_list` / `float_list` for comma-separated list arguments), `failures.py` (`raise
  fail(code, message)` deep in a tool's steps; the entry point returns `failure.result`),
  `ml_warnings.install()` (the ML libraries' routine warnings, silenced once per process;
  never `warnings.catch_warnings()` in code that runs in threads).

- **Imports:** a pack imports the toolkit only through `toolpacks/_toolkit.py`
  (`from .._toolkit import tool, error_result, ...`), never `agent_system.toolkit` or `toolkit`
  directly: the catalog loads the packs under the toolkit's own import root, and a pack that
  loaded the other copy of `ToolSpec` would silently drop out of the catalog.
- **`@tool(...)` with a full manifest:** the presets in `common/manifests.py` (`READ_ONLY`,
  `WRITES`; `INTERIM_HEAVY` only for the heavy tools Plan 5 moves to jobs) plus explicit
  `visibility`, `data_scopes` (`phi_read`/`phi_write` whenever patient identifiers are read or
  written), `resources` and `timeout_s` — heavy tools included (dispatch enforces it, and
  `test_every_heavy_tool_states_a_budget_it_can_finish_in` checks it). Graph-visible tools declare ports (`PortSpec`, input
  `param` = the handler argument; output `extract`). Tool names never change.
- **Identity comes from the context**, never the environment: `domain_api.session_id()`,
  `run_id()`, `attempt_id()`, `node_id()`, `agent_type()`, and `common/workspace.py`
  (`workspace_dir()`, `WorkspacePath`). No module constant may hold an identity.
- **HTTP:** the Application API only through `common/domain_api`: `request()` (decoded body,
  `DomainApiError` on failure) or `send()` (the raw response, for a caller that reads non-JSON
  bodies or error details itself) — both use the shared client, explicit timeouts, and the D24
  write-path header (`X-Attempt-Id`, or `X-Run-Id` for a job) on every write. A value in a URL
  path goes through `domain_api.segment()`. `db_reads/_api.py` (built on `send()`) turns a read
  into an `error_result` instead of raising. Other services (MONAI Label) use
  `common/http_client.shared_client()`; no pack creates its own `httpx.AsyncClient`.
  URLs from `common/service_urls.py` (`app_api_url()`, `inference_api_url()`), settings from
  `common/settings.py` — both read when used, never at import.
- **Failures return `toolkit.results.error_result(code, message, **extra)`** (snake_case codes;
  `domain_api.error_result_from(exc)` maps a `DomainApiError`). Never a bare
  `{"status": "error"}`, never a traceback or a 5xx body in what the model sees. A success keeps
  the tool's existing result shape.
- **Events:** dispatch already sends `tool_started` / `tool_finished` / `error` for every call;
  a tool adds only `emit("tool_log" | "tool_progress", ...)` / `emit_progress(...)`, and frontend
  view updates come from its `on_result` hook (`common/views.py`), not from the tool body.
- **New light tool -> add a success and an error case** to the Definition of Ready table.

### Frontend

- TypeScript `.tsx`/`.ts` for new code (older MONAI-label files are still `.js` — leave them).
  Config is not strict and `any` appears in existing code; there is no no-`any` rule.
- Prettier: 100 cols, 2 spaces, single quotes, semicolons, `arrowParens: avoid`,
  `singleAttributePerLine: true`, `trailingComma: es5`. ESLint adds `curly: error`.
- Feature-folder layout under `extensions/research-playground/src/components/PlaygroundCanvas/`:
  `views/<feature>/` holds that view's `types.ts`, `utils.ts`, `Shared.tsx`; the view component
  itself sits in `views/`. Nodes, edges, and log renderers are registry-based
  (`AgentRegistry.tsx`, `logs/LogRendererRegistry.tsx`) — extend the registry, don't branch.
- **Never hardcode service URLs.** Use `platform/app/src/utils/runtimeUrls.ts`
  (`getAppApiBaseUrl`, `getAgentsBaseUrl`, `getAgentsWsUrl`, `getInferenceBaseUrl`) or
  `extensions/research-playground/src/config.ts`. Both auto-switch between direct dev ports
  and same-origin production prefixes and honour `REACT_APP_*` overrides. Note there is a
  second, narrower copy at `extensions/monai-label/src/utils/runtimeUrls.ts` exporting only
  `getAppApiBaseUrl` / `getAgentsApiBaseUrl` and overriding via `window.config` instead of
  `REACT_APP_*` — when changing URL resolution, check both.

### Tests

Unit/contract/pipeline tests must not contact Ollama, the DB, Orthanc, MONAI, or the network;
mock model clients and MCP discovery (legacy AutoGen tests), and the Application API with the
`fake_domain_api` respx fixture (tool-pack tests; fixtures in `tests/support/toolkit_fixtures.py`:
call a tool through dispatch with `call_tool` and a `make_context(...)`). Synthetic sample data
lives in `Backend/tests/fixtures/sample_data/`. Write only to `tmp_path` or the `workspace_dir` fixture —
`Backend/conftest.py` redirects every storage env var to a temp tree at import time. Use
synthetic data only, never patient data. Every live-service, LLM, GPU, or slow test needs its
marker (`pytest.ini` runs with `--strict-markers`). Assert agent handoffs as a persisted schema
or explicit contract.

## Gotchas

- **`cubeai-docs/` has stale claims.** Verified corrections: the frontend is React **17** +
  **Webpack** (docs say React 18 + Vite); `PlaygroundCanvas` lives in
  `extensions/research-playground/`, not `platform/app/src/components/`; the default LLM model
  is `gpt-oss:120b-cloud` via `OLLAMA_MODEL` (`agent-system/README.md` still says
  `deepseek-v3.1:671b-cloud`); `agent-system/README.md` describes a legacy
  `agents_workspace/<id>/` layout superseded by `Backend/storage/hypotheses/workspace_<id>/`.
  Trust the code and `system-overview.md` over the subsystem READMEs.
- **`deploy/.env` is required** — copy from `deploy/.env.example`. It is currently _not_
  gitignored (the `.env` rule is commented out in `.gitignore`); never commit real credentials.
- **`COMPOSE_PROJECT_DIR` must be the absolute repo root** or every bind mount breaks. The
  launchers set it; manual `docker compose` invocations must set it themselves.
- **The launchers' Python probe must _execute_ the interpreter, not just resolve it.** Windows
  ships zero-byte *app execution alias* stubs for `python.exe`/`python3.exe` in
  `%LOCALAPPDATA%\Microsoft\WindowsApps`, which is on `PATH` by default — so a bare
  `Get-Command python` succeeds on a machine with no Python at all. Running the stub prints
  "Python was not found..." and exits **9009**, which made `migrate_storage.py --auto` look like
  a failed migration and `Stop-Launch` killed the launcher before any container started, with the
  misleading `[ERROR] Storage migration failed ... a rollback script was written` (no rollback was
  written; any files in `_migrated-legacy/` are from an older successful run). Both launchers now
  gate on `Test-ProcessSucceeds -Arguments "-c pass"` — **do not "simplify" that back to
  `Get-Command`.** The migration itself is a genuine no-op unless one of the nine `LEGACY_PROBES`
  in `Backend/scripts/migrate_storage.py` holds data, so skipping it on a Python-less machine is
  correct. `launcher.linux.sh` has the same probe but no Store aliases, so this is Windows-only.
- **Never `docker compose down -v`** as a troubleshooting step — it destroys the PostgreSQL and
  Orthanc named volumes.
- **Python deps:** Python 3.13 (`Backend/agent_system/.python-version`); install
  `Backend/agent_system/requirements.txt`, then PyRadiomics from source at the commit
  `deploy/Dockerfile.agents` pins. There are no late numeric pins any more, and nothing installs
  PyCaret. `Backend/tests/runtime/test_image_smoke.py` checks the built image (it skips outside a
  container). The MONAI Label image stays on Python 3.10.
- `Backend/conftest.py` deliberately reorders `sys.path` so `Backend/monailabel/` does not shadow
  the installed `monailabel` package; leave that block alone. Both `agent_system.*` and legacy
  bare (`agents.*`, `services.*`) imports are supported (the old `agent_system/tools/` package
  is gone). The tool packs follow whichever root the toolkit was imported under — see
  `toolpacks/_toolkit.py`.
- Agent workspace paths and the `WAITING_FOR_RESOURCE` status are frequently misread — see
  Architecture Notes before "fixing" either.
- The root `package.json` (React 19 / Tailwind, no scripts) is unrelated to the OHIF monorepo;
  the real frontend manifest is `Frontend/OHIF-Viewer/package.json`.
- **CI/CD runs on every branch, on push only** (a PR shows its head commit's checks, so there
  is no `pull_request` trigger and nothing runs twice). No job depends on `main` or on a published
  `latest`:
  - `backend-tests.yml` first builds the agents image **from the pushed commit**
    (`build-image.yml`) and runs `deterministic-tests` (the gate, Python 3.13, including the
    toolkit and tool-pack tests and the image smoke test), `live-db-tests` (the
    `run_live_db_tests.ps1` suites against a `postgres:18-alpine` service; fails if any of them
    skipped) and `agent-regression-tests` inside it. Only after those pass are the agents (amd64 + arm64) and application-api images
    published. `radiology-regression-tests` still uses the frozen
    `cubeai1/custom-monailabel:v1.0-cpu`.
  - `frontend-tests.yml` installs from `Frontend/OHIF-Viewer/yarn.lock` (tracked; the rest of
    `**/yarn.lock` stays ignored), fails if `yarn install` changes it (Yarn 1's
    `--frozen-lockfile` wrongly rejects this lockfile), runs `yarn test:unit:ci`, then builds
    the webapp image. `docker-build-monai.yml` builds the GPU image on its own paths.
  - **Registries:** every build goes to GHCR (`ghcr.io/<owner>/<image>:<sha>` and `:<branch>`,
    MONAI with `-gpu`), pruned weekly by `cleanup-ci-images.yml`. Docker Hub gets an image only
    from the branch in the `LATEST_BRANCH` repository variable (default `New-Architecture`;
    change the variable, not the workflows), from a `v*.*.*` tag, or from a manual MONAI tag —
    and only that branch moves `latest`, which compose pulls.
- External dependencies that must be up for full function: Docker, Ollama (or a configured
  OpenAI/Azure endpoint), and for GPU mode NVIDIA drivers + Container Toolkit. SAM2 checkpoints
  must be downloaded manually into `Backend/apps/radiology/sam2/checkpoints/`.
