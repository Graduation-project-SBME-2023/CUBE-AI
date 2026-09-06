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

- **Backend:** Python 3.11.3, FastAPI, async SQLAlchemy + asyncpg, Alembic, Pydantic v2
  (`pydantic-settings`), AutoGen (`autogen_agentchat` `GraphFlow`/`DiGraphBuilder`), FastMCP,
  PyRadiomics, PyCaret, MONAI Label / nnUNetv2 / VISTA3D / SAM2 / VoxTell, PyTorch.
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

Manual dev startup (`COMPOSE_PROJECT_DIR` **must** be the absolute repo root):

```bash
export COMPOSE_PROJECT_DIR="$(pwd)"   # PowerShell: $env:COMPOSE_PROJECT_DIR = (Get-Location).Path
docker compose --env-file deploy/.env -f deploy/docker-compose.backend.cpu.yml -f deploy/docker-compose.backend.dev.yml up
```

Swap `.cpu.` for `.gpu.` for GPU. Overlays: `backend.dev` (source-mounted, hot reload),
`backend.prod` (removes published backend ports), `frontend.prod` (OpenResty + built OHIF).

### Backend tests

`Backend/tests/run_tests.ps1` is the canonical runner; `-Runtime docker` (default) uses the
project images, so no local Python is needed.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File Backend/tests/run_tests.ps1 -Suite deterministic
```

Suites: `deterministic` (unit + contract + pipeline — **the PR gate**), `agent-regression`,
`radiology`, `integration` (needs running services), `all`, `list`. Flags: `-Coverage`,
`-Runtime local -Python <exe>`. JUnit goes to `Backend/test-results/`, coverage to
`Backend/coverage.xml` and `Backend/htmlcov/`.

Direct pytest (always pass `-c Backend/pytest.ini`; markers are strict):

```bash
python -m pytest -c Backend/pytest.ini Backend/tests/unit Backend/tests/contracts Backend/tests/pipeline -m "unit or contract or pipeline"
```

```bash
python -m pytest -c Backend/pytest.ini Backend/tests/unit/agents/test_modeling_agent.py
```

```bash
python -m pytest -c Backend/pytest.ini Backend/tests/unit -k "test_name"
```

Test deps: `python -m pip install -r Backend/requirements/test.txt` (plus
`Backend/agent_system/requirements.txt` for the runtime).

### Frontend

```bash
cd Frontend/OHIF-Viewer && yarn install
```

```bash
yarn dev              # webpack dev server on :3000
```

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

### Database

```bash
cd Backend && alembic -c application_api/alembic.ini upgrade head
```

```bash
cd Backend && alembic -c application_api/alembic.ini revision --autogenerate -m "message"
```

Compose runs one-shot `db-migrate` then `db-seed` before `application-api`.

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

### Generated-data boundary

**All** generated backend data lives under `Backend/storage/` and must be resolved through
`Backend/storage_paths.py` (`workspace()`, `workspace_subpath()`, `ws_sub()`,
`ensure_storage_layout()`). Never assemble runtime paths ad hoc, and never write runtime
artifacts (`*.nii.gz`, `*.pt`, `*.gif`, `workspace_*`, `execution_*.log`, ...) next to source —
`check_storage_boundary.ps1` fails CI and blocks commits if you do. Session data lives at
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
reads/writes, planner output and `write_agent_output()` payloads. Agents share state through
the session workspace and `scratchpad.json`, and act through MCP tools in `Backend/mcp/tools/`
(population, radiomics, modeling, LaTeX, scratchpad, DB reads). Per-agent I/O contracts are in
`cubeai-docs/agent-system/io-contracts/`.

### Sessions, WebSocket, and scheduling

Everything is scoped to a `session_id`. `/ws?session_id=<id>` is mandatory; the server only
delivers a session's events to its own sockets, and the client
(`platform/app/src/utils/WebsocketService.ts`) drops cross-session traffic, dedupes by
`event_id`, and uses bounded reconnect/pending buffers. Server envelope:
`{version, event_id, sequence, session_id, run_id, type, payload, timestamp, replaying}`.
Pipeline start/pause/resume/stop are **REST**, not WebSocket events.

Concurrency is two-layered: up to `PIPELINE_MAX_CONCURRENCY` sessions run, but heavy stages
take process-local leases (`gpu_segmentation`, `cpu_heavy`, capacity `1` by default).
`WAITING_FOR_RESOURCE` means "queued for hardware" — it is **not** a request for user input.

Without Redis, socket routing and capacity queues are process-local: **run exactly one Agent
System worker.** Multi-worker needs a shared broker first.

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
- File naming is inconsistent in `application_api` (`Taskmessage.py`, `Wslog.py` alongside
  snake_case) — match the existing file you are editing rather than renaming.

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
  and same-origin production prefixes and honour `REACT_APP_*` overrides.

### Tests

Unit/contract/pipeline tests must not contact Ollama, the DB, Orthanc, MONAI, or the network;
mock model clients and MCP discovery. Write only to `tmp_path` or the `workspace_dir` fixture —
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
- **Never `docker compose down -v`** as a troubleshooting step — it destroys the PostgreSQL and
  Orthanc named volumes.
- **Python dep install order matters:** install `Backend/agent_system/requirements.txt`, then
  PyRadiomics from source, then `numpy==1.26.4 pandas==2.1.4 pycaret==3.3.2` **last**, or they
  get overwritten. Python must be 3.11.3 (`Backend/agent_system/.python-version`).
- `Backend/conftest.py` deliberately reorders `sys.path` so `Backend/monailabel/` does not shadow
  the installed `monailabel` package; leave that block alone. Both `agent_system.*` and legacy
  bare (`agents.*`, `services.*`, `tools.*`) imports are supported.
- Agent workspace paths and the `WAITING_FOR_RESOURCE` status are frequently misread — see
  Architecture Notes before "fixing" either.
- The root `package.json` (React 19 / Tailwind, no scripts) is unrelated to the OHIF monorepo;
  the real frontend manifest is `Frontend/OHIF-Viewer/package.json`.
- Backend CI runs inside the published project images (`cubeai1/agents-app:latest`,
  `cubeai1/custom-monailabel:v1.0-cpu`); the storage-boundary job runs on `windows-latest`.
- External dependencies that must be up for full function: Docker, Ollama (or a configured
  OpenAI/Azure endpoint), and for GPU mode NVIDIA drivers + Container Toolkit. SAM2 checkpoints
  must be downloaded manually into `Backend/apps/radiology/sam2/checkpoints/`.
