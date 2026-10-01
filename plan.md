# CUBE.AI merged roadmap: Agent Harness + Tool Platform

## Context

Two design documents describe the next version of CUBE.AI:

- **`system_plan.md` (Agent Harness Architecture)** describes *how the system behaves*. A general **Main Agent** decides what should happen. A live, versioned **Session Graph** records the work that was submitted. An **Executor** is the only component that runs work. **Runtime Guardrails** own permissions, PHI, approvals and budgets. It also defines Plan/Auto modes, a Recovery Agent and Skills.
- **`tools_plan.md` (Tool Platform)** describes *what the system is built from*. It has a framework-neutral **Toolkit** (`ToolSpec`, `AgentSpec`, registries, catalog), tool packs, **Celery + Redis jobs** for heavy tools, a **sandbox**, an **outbound MCP edge**, and fixes for every known job bug.

The two documents overlap: both define registries, tool metadata, agents and execution. They also conflict in several places: package names, modeling, synchronous delegation versus asynchronous submission, and the fixed linear plan contract. This roadmap merges them into **one ordered set of 18 small plans (Plan 0 to Plan 17)**, each with phases. The toolkit and job work become the foundation. The harness, the graph and the Executor are built on top of that foundation.

**Decisions taken with you for this merge:**
- **`run_modeling` is a tool.** There is no `modeling_agent` (system_plan wins over tools_plan here).
- **LangGraph is the agent engine** for every LLM agent, including the Main Agent. The Executor, Diff Engine and Validator stay custom code.
- The scope includes the **cut-over and AutoGen removal** (Plan 17). The frontend Surface and the evaluation harness are **not** in this roadmap.
- **Python 3.13 for every image we build** (Agent System, compute worker, Application API), done first in Plan 0. The MONAI Label image keeps its own Python 3.10.
- **PyCaret is removed.** Modeling runs on scikit-learn, with LightGBM, XGBoost and SHAP. The 5 PyCaret tools are rewritten on that engine and **renamed** in Plan 7 (the only rename besides `save_literature_output`).

**What exists today (checked in the code):**
- **The pipeline:** AutoGen `GraphFlow` runs a fixed linear list. The planning agents write `plans` and `plan_steps`. `POST /plans/session/{id}/approve` creates one `agent_runs` row per step. `CANONICAL_AGENT_TYPES` (`application_api/core/agent_types.py:6-13`) is checked only there (`repositories/plan.py:136`).
- **Runs:** `agent_runs` has a unique `step_id` and the status set `pending|running|completed|failed`. A DB trigger enforces the transitions (`migrations/versions/f1f0a2c3d4e5_v1_f_execution_spine.py:100-108`).
- **Producer checks:** domain rows (`cohorts`, `segmentation_runs`, `radiomics_features`, `feature_sets`, `model_results`, `reports`, `artifacts`) point to `created_by_agent_run_id`. Both `require_producer_run` (`repositories/radiomics.py:59-78`) and the `domain_rows_enforce_producer` trigger (`i1f0a2c3d4e5_v1_i_feature_extraction.py:51-75`) check the producer's `step.agent_type`.
- **Sessions:** the status set is `planning|ready|running|pausing|paused|completed|failed|archived` (`db/models/sessions.py:11-20`). There is no execution mode, no privacy setting, no graph version and no approvals table. `plans.approved_at` is the only approval.
- **User input:** `POST /session/{id}/input` feeds `ConnectionManager.message_queues` (`socket_store.py:86-98,187`). Only the planner reads it (`wait_for_user_message`, `socket_store.py:224`).
- **Models:** there is one global `ClientConfig` (`client_config.py:163-232`). Its default is Ollama with `gpt-oss:120b-cloud`, which is a **cloud** model. Retries are in `add_model_client_retries` (`:95`).
- **Human gates:** cohort approval (`/populations/approve-cohort`, which freezes the cohort), segmentation review (the `/segmentation-review/wait` long-poll) and plan approval (`/run`).
- **Internal agents:** the segmentation module builds its internal agents itself (`segmentation/module.py:96-133,1375-1376`, using `list_models` as a tool). The literature agent uses 6 AutoGen `FunctionTool`s (`literature_review_agent.py:1499-1525`).
- **The canvas** builds nodes from the plan `steps`, maps events to nodes by `source`, and starts work with `/plan` and `/run`.
- **Import path:** `agent_system` is `sys.path[0]` in the container, and Python does not preload `types` (checked on 3.11.3 and on `python:3.13-slim`). A top-level `agent_system/types/` package would therefore break `import asyncio` in production.
- **Runtime:**
  - the agents image is `python:3.11.3-slim` (`deploy/Dockerfile.agents`, `Backend/agent_system/.python-version`); the Application API image is `python:3.11-slim`;
  - PyCaret 3.3.2 is the newest stable PyCaret (April 2024). It pins `numpy<1.27`, `pandas<2.2`, `scipy<=1.11.4` and `sktime==0.26.0`, and sktime declares `requires_python <3.13`. That is why `Dockerfile.agents` reinstalls `numpy==1.26.4 pandas==2.1.4 pycaret==3.3.2` last, and why the image cannot move to Python 3.13;
  - PyCaret is used only in `Backend/mcp/tools/modeling/` (`classification.py`, `regression.py`, `evaluation.py`, `refit.py`, `refit_core.py`). `model_results` already records each estimator as a class path (`parameters.estimator`, e.g. `sklearn.ensemble.RandomForestClassifier`) plus the PyCaret model id (`parameters.pycaret_model_id`);
  - the MONAI Label image is `projectmonai/monailabel:0.8.5` on Python 3.10, and it copies `Backend/storage_paths.py`;
  - **checked on 2026-09-30:** without PyCaret, the whole agents dependency set (AutoGen included) resolves on Linux for Python 3.13. PyRadiomics built from source (commit `8ed5793`) passes its 3,555 upstream tests on 3.13, and a synthetic extraction with the `features.py` settings gives 1,409 features identical to 3.14.

## Principles (from both documents)

1. **The model owns the strategy; the harness owns execution, state, permissions and safety.** There is no router, no intent classifier, no intake agent, and no rule that picks a strategy from complexity or step count.
2. **Orchestration is not execution.** The Main Agent and the Composer only *describe* work. Only the Executor *runs* it.
3. **Long-running work is asynchronous.** Submitting work returns `RunAccepted` immediately, and the Main Agent never waits for a node.
4. **The Session Graph is live.** It is versioned, can change during execution, and is never "finished".
5. **Guardrails decide boundaries, never reasoning.** Each check returns exactly one of `allowed | approval_required | permission_denied | invalid | resource_unavailable`.
6. **A tool is a callable action (`ToolSpec`).** An agent is a definition (`AgentSpec`) and never a tool. Delegation is a tool.
7. **Heavy compute runs as Celery jobs** behind one `JobClient` contract. Isolation follows trust.

## Compatibility decisions (how the two plans fit together)

| # | Topic | system_plan says | tools_plan says | Merged decision |
|---|---|---|---|---|
| 1 | Package layout | `tools/registry.py`, `types/artifacts.py`, `agents/<id>/agent.md`, `harness/context.py` | `toolkit/`, `toolpacks/`, `agentpacks/`; no `platform`, `core`, `tools` or `mcp` names | Keep the tools_plan names. See the rename list below this table. |
| 2 | The word "harness" | The session layer (Main loop, guardrails, inbox) | `RunHarness` = per-run bookkeeping | "Harness" now means only the system_plan session layer. `RunHarness`, `node_resolver` and `DirectToolExecutor` move into `execution/node_runner.py` and `catalog.resolve()`. |
| 3 | Tool metadata | Manifest: typed inputs/outputs, `side_effects`, `cost`, `resources`, `phi`, `ui` | `ToolSpec`: execution, trust, isolation, data_scopes, visibility, budgets, hooks | One `ToolSpec` with both sets of fields. Derived fields and consistency rules are checked by `catalog.validate()` (Phase 1.1). |
| 4 | Who may call what | Main calls tools directly, or submits agents and tools as nodes | `visibility = planner \| agent \| harness` | `visibility` is a **set** of `main`, `graph`, `agent` and `harness`. `planner` is renamed `graph` (it may become a Tool Node). Main may call a tool directly only if it is `inline` and not `heavy`. Everything else runs as a node. |
| 5 | Delegation | Main never waits; the Planner is an Agent-as-Tool | `run_agent` waits for the child agent | Main has **no** `run_agent`. It uses `execute` / `graph_apply`, which create an Agent Node and return `RunAccepted`. `run_agent` stays only for execution agents that call their internal agents, inside a node that is already tracked. `plan` and `recover` are short agent-as-tool calls: they return proposals and never execute anything. |
| 6 | Modeling | `run_modeling` tool; no Modeling Agent | `modeling_agent` stays an agent | `run_modeling` is a job tool (manifest from system §15). `modeling_agent` is not created, and its prompt becomes the `modeling` skill. PyCaret is removed: the 5 modeling tools are rewritten on a scikit-learn engine, renamed, and stay graph-visible job tools (Plan 7). |
| 7 | Agent engine | Plain loop (pseudocode) | `AgentEngine` protocol with a fake; LangGraph later | `LangGraphAgentEngine` implements `AgentEngine` for all LLM agents. The inbox loop, Executor, Diff Engine and Validator are plain code, because a compiled LangGraph `StateGraph` cannot represent a graph that changes while it runs. There is no LangGraph checkpointer in PostgreSQL (only the Application API has `DATABASE_URL`). Each turn and each node run starts from the context that the Context Manager rebuilds. |
| 8 | Runs | Nodes have Runs | Job ids use `agent_run_id`; identity comes from `ToolContext` | `agent_runs` becomes the run table for **every** node run, agent or tool. `step_id` becomes nullable, and new columns `node_id`, `attempt`, `producer_kind` and `producer_name` are added. All domain FKs, the scratchpad tools, the H20 check and the job-id formula keep working. |
| 9 | Plan contract | Open graph, no fixed order | `planner_steps() == CANONICAL_AGENT_TYPES` | This contract is dropped. The Validator and the registry decide what is valid. A contract test checks the producer allowlists in the Application API against the catalog. `plans` and `plan_steps` become legacy tables. |
| 10 | Session status | `idle \| running \| awaiting_approval \| blocked`; nothing is ever "finished" | Uses the current statuses | Add `idle`, `awaiting_approval` and `blocked`. Keep `pausing`, `paused`, `failed` and `archived`. Sessions created by the harness never move to `completed`. |
| 11 | Resources | The Executor acquires `resources` (e.g. `cpu_heavy`) | `resource_class` maps to a Celery queue; J5 asks for named pools | `resources` is the manifest field. `resource_class` is derived from it: `cpu_heavy`→`compute.cpu`, `gpu`→`compute.gpu`, `highmem`→`compute.highmem`. Executor pools are set in `config/resources.yaml`. A node waiting for a pool has the status `queued`. |
| 12 | Cache vs job dedupe | A fingerprint drives cache reuse | `job_id = sha256(agent_run_id\|job_name\|args)` | Two keys with two jobs. The node **fingerprint** (structure + params + upstream fingerprints) decides cache reuse across graph versions. The **job id** re-attaches to the same run after a restart. |
| 13 | Human gates | Approvals Panel; Plan mode | Not covered | A new `approvals` table holds plan-mode and mandatory approvals. The gates that already exist inside nodes (cohort approval, segmentation review) stay inside those nodes, and the node shows `waiting_for_user`. |
| 14 | PHI | `phi` flag; PHI-Local mode | `data_scopes`; MCP `allow_phi` | `phi` is derived from `data_scopes`. Artifacts carry a `phi` label. The Model Gateway filters models by locality, and the MCP edge refuses PHI. `gpt-oss:120b-cloud` counts as **external**. |
| 15 | Events | Graph Canvas; important events | `source` = agent type; `EventSink` | `source` = the node's ref name (an agent type or a tool name), or `main_agent` for the Main Agent. New graph and approval event types are registered in `event_registry.py`. `pipeline_agent_status` is still emitted for agent node runs, so the current canvas keeps partly working. |
| 16 | Internal agents | Not mentioned | AgentSpecs only | Internal agents run through `AgentEngine`. The segmentation module calls the engine instead of AutoGen `AssistantAgent`. The 6 literature `FunctionTool`s become registered tools. |
| 17 | Runtime | Not covered | Python 3.11.3; PyCaret kept | Python 3.13 for our images (Plan 0). PyCaret removed; modeling on scikit-learn (Plan 7). MONAI stays on its own Python 3.10 image. |

**Renames for decision 1 (layout):**

| system_plan path | Merged path | Why |
|---|---|---|
| `types/artifacts.py` | `toolkit/artifacts/` | A `types` package shadows the stdlib `types` module. |
| `tools/registry.py` | `toolkit/tools/registry.py` | Legacy bare `tools.*` imports still exist. |
| `agents/<id>/agent.md` | `agentpacks/<pack>/<agent_type>/agent.md` | `agents/` is the legacy AutoGen package, deleted in Plan 17. |
| `harness/context.py` | `harness/context_manager.py` | `toolkit/context.py` already holds `ToolContext`. |
| `config/` | `config/`, data only, with no `__init__.py` | `config` is too generic a name for a regular package. |

## Global rules (apply to every plan)

- **AutoGen is frozen until Plan 17.** No effort goes into these files, and they may break:
  - `agents/execution_agents/*_agent.py`, `agents/planning_agents/`, `agents/literature_review_agent/literature_review_agent.py`, `agents/run_bound_agent.py`;
  - `teams/`, `api/runners.py`, `services/pipeline_monitor.py`, `task_manager/`, `system_prompts/`.

  The segmentation domain module (`agents/execution_agents/segmentation/`) is **not** frozen; it is domain code.
- **Only the new work is tested.** The project does not need to run after any phase or plan, and it will not be run end-to-end.
- **No MCP tool is renamed**, and no tool's success result shape changes. The only renames are `save_output` → `save_literature_output` (Plan 2) and the 5 `pycaret_*` tools (Plan 7); their success result shapes still do not change. Agent ids stay the same. For example, `study_population_planner_agent` keeps its id, and "Population Agent" is only its display name.
- **Python 3.13.** New code targets Python 3.13. Code that also runs in the MONAI image (`Backend/storage_paths.py` and every file `Dockerfile.backend.gpu` copies) must stay compatible with Python 3.10.
- **No PyCaret.** No new code imports `pycaret`, and no new code depends on PyCaret objects or PyCaret setup arguments.
- **Forbidden package names:** `platform`, `core`, `tools`, `mcp` and `types`, and no regular package named `config`.
- **Only the Application API gets `DATABASE_URL`.** All graph, run, artifact and approval state goes through its HTTP API.
- **Generated files** are resolved through `Backend/storage_paths.py`. Graph or inbox state is never written to workspace files, and the write guard is respected.
- **Run exactly one Agent System worker.** Main loops, executors and inboxes are process-local asyncio objects. Everything durable lives in the Application API or in Redis.
- **Tests** use synthetic data. Unit and contract tests use fake chat models, fakeredis and in-memory backends, and never touch the network.
- **Strategy-free harness:** no harness rule may read the request text or count steps to choose a strategy. A unit test scans the guardrail rules for this.

## Architecture after the merge

```
CONTROL PLANE  (agent_system/harness, agent_system/planning)
  User -> POST /session/{id}/input -> Session Inbox -> Main Loop (main_agent on LangGraphAgentEngine)
     Main's tools:  main-visible light tools (dispatch.invoke, inline)
                    control tools: execute, graph_apply, graph_inspect, registry_search/describe,
                                   plan (-> Composer -> Validator), recover (-> Recovery Agent),
                                   node_cancel, node_message, artifact_read, skill_load
     every action -> Runtime Guardrails -> allowed | approval_required | permission_denied | invalid | resource_unavailable
                                    |
                                    |  GraphDiff(base_version)  -> RunAccepted {node_id, run_id, status}
                                    v
EXECUTION PLANE  (agent_system/execution)
  Diff Engine --CAS--> Session State Store (Application API: graph_versions, graph_nodes,
                                            agent_runs, graph_artifacts, approvals)
  Executor -> Node Runner -+-> Agent Node: AgentSpec on LangGraphAgentEngine (run_agent -> internal agents)
                           +-> Tool Node:  dispatch.invoke -> inline
                                                          -> job      (JobClient -> Celery/Redis -> compute worker -> sandbox runner)
                                                          -> external (MCP edge, Plan 14)
  Watchdog (heartbeats, timeouts, OOM, interrupted)
  important events (failure, expectation violated, graph idle, approval decided) -> Session Inbox
```

## Target layout

```
Backend/
  agent_system/
    toolkit/                      Plan 1 ✅ (no AutoGen / LangGraph imports, except engines/)
      manifest.py registry.py ports.py context.py events.py results.py catalog.py
      tools/spec.py tools/consistency.py tools/registry.py
      agents/spec.py agents/registry.py agents/engine.py agents/loader.py (Plan 3)
      artifacts/base.py artifacts/builtin.py                    (system_plan "types/artifacts.py")
      runtime/dispatch.py runtime/inline.py runtime/cancel.py runtime/session_sink.py
      runtime/job.py (Plan 5) runtime/external.py (Plan 14)
      delegation/run_agent.py delegation/agent_as_tool.py
      engines/langgraph_engine.py                               (Plan 9)
    toolpacks/                    Plans 2, 7, 12, 14 (discovered automatically)
      common/ population/ db_reads/ segmentation/ reporting/ bookkeeping/ literature/   (Plan 2)
      modeling/ (incl. run_modeling; engine/ on scikit-learn) radiomics/                  (Plan 7)
      control/                                                                            (Plans 12, 13)
      external_mcp/                                                                       (Plan 14)
    agentpacks/
      core/{main_agent,composer,recovery_agent}/agent.md                                 (Plan 3)
      research/<agent_type>/agent.md (+ hooks.py)                                         (Plan 3)
    skills/*.md                                                                           (Plan 3)
    harness/                      Plans 9, 11, 12, 13
      model_gateway.py guardrails.py approvals.py inbox.py context_manager.py
      skills_loader.py main_loop.py session_runtime.py privacy.py
    planning/                     Plans 8, 12: graph_model.py validator.py proposals.py
    execution/                    Plans 6, 8, 10, 13
      graph_store.py fingerprint.py diff_engine.py executor.py node_runner.py
      resources.py expectations.py watchdog.py recovery.py
    config/                       data only: models.yaml (9) resources.yaml (10) mcp_servers.yaml (14)
  application_api/                Plan 6: graph models, repositories, schemas, routes, migration "o"
  jobs_core/                      Plan 5 (backends/monai_http.py: Plan 15)
  compute_worker/                 Plan 5 (Celery app)
  sandbox/                        Plan 4
```

## Milestones, order and dependencies

| # | Plan | Milestone | Depends on | Comes from | Status |
|---|---|---|---|---|---|
| 0 | Runtime: Python 3.13, PyCaret removed | M0 Runtime | – | your decision (this merge) | Not started |
| 1 | Toolkit Core | M1 Toolkit | – | tools P1; system Phase 1 (registries, artifact types, discovery) | ✅ Done (1.1–1.5) |
| 2 | Tool Packs | M1 | 1 | tools P2 | Next |
| 3 | Agent Definitions and Skills | M1 | 1, 2 | tools P3; system §29-30 | Not started |
| 4 | Sandbox Foundation | M2 Compute | 1 | tools P4 | Not started |
| 5 | Job Platform (Celery + Redis) | M2 | 1, 4 | tools P5 | Not started |
| 6 | Session State Store (Application API) | M3 Harness | – | system Phase 1 (State, Graph Versions) | Not started |
| 7 | Modeling Engine, Heavy Tools as Jobs + `run_modeling` | M2 | 0, 2, 5, 6 | tools P6; system `run_modeling`; your decision (PyCaret removed) | Not started |
| 8 | Validator and Diff Engine | M3 | 1, 6 | system Phase 1 (Validator), Phase 2 (Diff, Affected Set) | Not started |
| 9 | LangGraph Engine and Model Gateway | M3 | 1, 3 | your decision; system §4 (Model Gateway) | Not started |
| 10 | Executor, Node Runner, Watchdog | M3 | 5, 6, 8, 9 | system Phases 1–3 (Executor, Cache, Live changes, Watchdog) | Not started |
| 11 | Runtime Guardrails, Modes, Approvals | M3 | 4, 6, 8, 10 | system §10, §31 | Not started |
| 12 | Session Layer: Inbox, Main Loop, Context, Skills, Planner | M3 | 8, 9, 10, 11 | system Phase 1 (Main Loop, Context), Phase 2 (Planner) | Not started |
| 13 | Recovery and Privacy | M4 Hardening | 10, 11, 12 | system Phase 3 | Not started |
| 14 | MCP Outbound Edge | M4 | 1, 4, 11 | tools P7 | Not started |
| 15 | MONAI Jobs + Segmentation Client | M5 Job fixes | 5 (Phase 5.1 only) | tools P8 | Not started |
| 16 | Other Job Systems | M5 | 5, 15 | tools P9 | Not started |
| 17 | Cut-over, AutoGen Removal, Cleanup, Docs | M6 | all | tools P10; your decision | Not started |

**Progress (2026-10-01):** Plan 1 is done: all five phases, 505 toolkit tests passing on Python 3.13 (see "Plan 1 — as built" at the end of Plan 1). Plan 1 was done **before** Plan 0, so the order above was not followed. The toolkit is written for 3.13, but the agents image is still `python:3.11.3`. Until Plan 0 lands, run the toolkit tests from a local 3.13 venv: in Docker, the deterministic suite cannot collect `tests/unit/toolkit/`.

**Implementation order:** 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 15 → 16 → 17.

Plan 0 comes first so that every later plan is written and tested on Python 3.13. From Plan 0 until Plan 7, the moved `pycaret_*` tools cannot run (PyCaret is no longer installed). This is allowed by the global rule that the project does not need to run between plans.

Some plans can move:
- Plan 6 touches only the Application API, so it can start at any time.
- Plans 15 and 16 can run next to M3 as soon as Phase 5.1 is done.

**Relation to the system_plan phases (§35):** M3 covers system Phases 1–2 (Foundation and Live Graph), and M4 covers Phase 3 (Recovery and Privacy). Phase 4 (Scaling) is not in this roadmap.

---
---

# Plan 0 — Runtime: Python 3.13, PyCaret removed

**Goal:** every image we build runs Python 3.13, PyCaret and its old pins are gone, and the rest of the roadmap is written and tested on 3.13. The MONAI Label image is not touched.

## Phase 0.1 — Images and requirements

1. **`deploy/Dockerfile.agents`:**
   - `FROM python:3.13.15-slim` (pin the patch release, as the file pins `3.11.3` today);
   - delete the step that reinstalls `numpy==1.26.4 pandas==2.1.4 pycaret==3.3.2 shap` last; `shap` moves into the requirements file;
   - pin PyRadiomics to the tested commit: `git+https://github.com/AIM-Harvard/pyradiomics.git@8ed5793` (today it installs whatever `master` is);
   - keep the build tools (`gcc`, `g++`, `cmake`, `make`, `git`): PyRadiomics still compiles its C extensions.
2. **`Backend/agent_system/requirements.txt`** (keep **UTF-16 LE with CRLF**):
   - `numpy>=2.3,<=2.3.0` → `numpy>=2.3,<3` (PyRadiomics needs `numpy>=2.0`);
   - add `shap`, `lightgbm`, `xgboost` and `joblib` (the modeling engine, Plan 7); `scikit-learn`, `scipy`, `pandas` and `matplotlib` are already listed;
   - no `pycaret`, `sktime` or `pmdarima`.
3. **`Backend/agent_system/.python-version`:** `3.13.15`.
4. **`deploy/Dockerfile.application-api`:** `FROM python:3.13-slim`. Its packages are unpinned and all support 3.13.
5. **Not changed:** `deploy/Dockerfile.backend.gpu` and the MONAI images (`cubeai1/custom-monailabel:*`, Python 3.10). The `radiology` CI job keeps running in `cubeai1/custom-monailabel:v1.0-cpu`.

## Phase 0.2 — Verify the runtime

1. **Resolve check:** `uv pip compile` of the agents requirements for `--python-version 3.13 --python-platform x86_64-manylinux_2_28` succeeds. Resolve for Linux, not for the Windows host: pip evaluates `sys_platform == "win32"` markers against the host and reports false conflicts (`mcp` → `pywin32`).
2. **Image smoke test** (`tests/runtime/test_image_smoke.py`, marker `docker`), inside the built agents image:
   - `sys.version_info[:2] == (3, 13)`;
   - `import pycaret` fails;
   - `numpy`, `pandas`, `scipy`, `sklearn`, `lightgbm`, `xgboost`, `shap`, `SimpleITK` and `radiomics` import, and PyRadiomics' C extensions (`radiomics._cmatrices`, `radiomics._cshape`) load;
   - a synthetic PyRadiomics extraction with the `features.py` settings (`Original` and `Wavelet`) returns features.
3. **Known limitation, not a regression:** the `LBP3D` image type still produces no LBP features. PyRadiomics imports `scipy.special.sph_harm`, which SciPy 1.17 removed, and it also needs `trimesh`, which the current image does not install either. The default image types (`Original`, `Wavelet`) are not affected.

## Phase 0.3 — CI and docs

1. **CI:** rebuild and push `cubeai1/agents-app:latest` (`docker-build-agents.yml`). `backend-tests.yml` runs inside that image, so the deterministic suite moves to 3.13 with it.
2. **Tests:** no new marker is needed. The only test that runs PyCaret (`tests/unit/functions/test_model_refit.py:80`) already uses `pytest.importorskip("pycaret.classification")`, so it skips. Plan 7 replaces it.
3. **Docs:**
   - `CLAUDE.md`: Python 3.13 in the tech stack; remove the "install `numpy==1.26.4 pandas==2.1.4 pycaret==3.3.2` last" gotcha; add that the MONAI image stays on Python 3.10;
   - `Backend/agent_system/Setup.md`, `cubeai-docs/getting-started/README.md`, `cubeai-docs/backend/README.md` and `cubeai-docs/backend/testing.md`: 3.11 → 3.13.

**Tests:** the resolve check and the image smoke test above.

**Done when:** the agents and Application API images build on Python 3.13, the smoke test passes, and nothing installs PyCaret.

---

# Plan 1 — Toolkit Core ✅

**Goal:** a framework-neutral core for tools, agents, artifact types, context, events, dispatch and delegation.

**What changed from tools_plan Plan 1:**
- **Added:** the manifest fields, ports, artifact types, visibility sets, the compact index and the agent-as-tool factory.
- **Moved out:** `RunHarness`, `node_resolver` and `DirectToolExecutor` go to Plan 10. The `CANONICAL_AGENT_TYPES` contract is replaced (see Plan 6).

## Phase 1.1 — Contracts ✅

1. **`toolkit/tools/spec.py` — `ToolSpec`:**

   | Field | Meaning | From |
   |---|---|---|
   | `name`, `version`, `description`, `pack` | Identity. Packs: population, db_reads, segmentation, reporting, bookkeeping, literature, modeling, radiomics, delegation, control, external_mcp | both |
   | `handler` | An async function (first-party), or a closure (external) | tools |
   | `input_model` / `input_schema` | The params model built from the signature, or a raw JSON schema (external) | tools |
   | `inputs` | Ports filled from upstream artifacts: `{port: PortSpec(type, param)}`. `param` names the handler argument that receives the artifact ref. | system manifest `inputs` |
   | `outputs` | Ports the tool produces: `{port: PortSpec(type, extract)}`. `extract(result)` returns the artifact ref and its summary. | system manifest `outputs` |
   | `output_model` | Optional result model | tools |
   | `side_effects` | `none` \| `writes` \| `compute` | system §16 |
   | `cost` | `light` \| `medium` \| `heavy` | system §16 |
   | `resources` | Named pools: `llm`, `cpu_heavy`, `gpu`, `highmem`, `monai_inference`, `orthanc_io` | system; replaces tools `resource_class` |
   | `execution` | `inline` \| `job` \| `external` | tools |
   | `trust`, `isolation`, `data_scopes` | As in tools_plan (`TrustLevel`, `IsolationLevel`, `DataScope` enums live here) | tools |
   | `phi` | Derived: true when `data_scopes` contains `phi_read` or `phi_write` | system |
   | `ui` | The view type for the node, e.g. `modeling_results_view` | system |
   | `on_result` | Hook: result → UI view events | tools |
   | `visibility` | A subset of {`main`, `graph`, `agent`, `harness`} | merged |
   | `timeout_s`, `queue_timeout_s`, `priority`, `max_attempts` | Budgets and the retry cap | tools + system Watchdog |
   | `read_only`, `destructive`, `idempotent`, `critical` | As in tools_plan | tools |

   `resource_class` (the Celery queue) is a derived property: `cpu_heavy`→`compute.cpu`, `gpu`→`compute.gpu`, `highmem`→`compute.highmem`.
2. **Manifest consistency rules** (enforced in Phase 1.3):
   - `side_effects="none"` ⇔ `read_only=True` and no write scope (`phi_write`, `db_write`, `fs_write`);
   - `cost="heavy"` ⇒ `execution="job"`;
   - `execution="job"` ⇒ `resources` is not empty, and the args are JSON only;
   - `"main"` in `visibility` ⇒ `execution="inline"` and `cost` is not `heavy`. Most main tools are `light`. `plan` and `recover` are `medium`, with a hard timeout. Together with the `REQUIRES_NODE` guardrail, this enforces "Main never runs long work".
   - `"graph"` in `visibility` ⇒ at least one output port, and every input port's `param` exists in `input_model`.
3. **`toolkit/ports.py`:** `PortSpec(artifact_type, param=None, many=False, required=True, extract=None)`.
4. **`toolkit/agents/spec.py` — `AgentSpec`.** It is a **separate class and does not inherit from `ToolSpec`**.

   | Field | Meaning |
   |---|---|
   | `agent_type`, `version`, `description`, `kind` | `kind` is one of: `control` (main_agent, composer, recovery_agent); `execution` (can be a graph node); `internal` (reached only through `run_agent`) |
   | `system_prompt` | Text from `agent.md`, or a function of the context |
   | `allowed_tools` | A list of names, or a function of the context (for example, "all tools with `main` visibility") |
   | `delegates` | The internal agents it may start with `run_agent` |
   | `inputs` / `outputs` | Ports (execution agents only) |
   | `model_profile` | A Model Gateway profile (Plan 9) |
   | `max_tool_iterations`, `timeout_s`, `resources` (default `["llm"]`) | Budgets |
   | `completion_check`, `max_completion_retries`, `output_contract` | Completion rules |
   | `context_builder` | Builds the agent's first message from the node inputs (Plan 12) |
   | `ui`, `human_in_the_loop`, `parent` | The view type; whether the agent has user gates inside the node; the owner of an internal agent |

   These tools_plan fields are removed, because `kind` and `visibility` replace them: `planner_visible`, `phase`, `bootstrap`.
5. **`toolkit/context.py` — `ToolContext`:**
   - Fields: `session_id`, `agent_run_id` (the run row of the current node), `node_id`, `graph_version`, `step_id` (legacy, nullable), `agent_type`, `caller` (`main|agent|node|harness`), `subagent`, `depth`, `workspace_dir`, `tool_name`, `job_id`, `execution_mode`, `privacy_mode`, `event_sink`, `session_handle`, `mailbox`.
   - It is stored in a `ContextVar`, read with `current_context()`, and serialized with `to_payload()` / `from_payload()`. The payload leaves out `event_sink`, `session_handle` and `mailbox`.
6. **`toolkit/events.py`:**
   - Move `ToolEvent`, `EventType` and the payload classes here from `Backend/mcp/tool_logging.py`, and add an `EventSink` protocol.
   - **Rule:** `source` = the node's ref name (an agent type or a tool name), or `main_agent`. This keeps `PlaygroundCanvas.tsx:145-167` working.
   - New optional fields: `tool_name`, `subagent`, `job_id`, `node_id`.
7. **`toolkit/results.py`:**
   - `error_result(code, message, **extra)` → `{"status":"error","error_code","error_message",...}`;
   - `RunAccepted {node_id, run_id | None, status, graph_version, approval_id | None}`.

## Phase 1.2 — Artifact types (`toolkit/artifacts/`) ✅

1. **`base.py`:**
   - `ArtifactType(name, version, ref_model, summary_model, phi_default, parents)` and an `ArtifactTypeRegistry`;
   - `is_compatible(produced, expected)`: the names are the same, or `expected` is one of the produced type's `parents`.
2. **`builtin.py`:**

   | Type | Ref (what it points to) | Summary (read by expectations and by Main's context) |
   |---|---|---|
   | `Cohort` | `cohort_id` (`cohorts`) | `size`, `label_counts`, `frozen` |
   | `ImageSet` | `series_uids` | `n_series`, `modalities` |
   | `MaskSet` | `segmentation_run_id` (`segmentation_runs`) | `n_masks`, `n_failed`, `review_status` |
   | `FeatureTable` | `feature_set_id` (`feature_sets`) | `n_rows`, `n_features` |
   | `FeatureImportance` | `feature_set_id`, `method` | `top_features` |
   | `ModelingOutput` | `model_result_ids` (`model_results`) | `metric_name`, `best_metric`, `n_models` |
   | `LiteratureContext` | `literature_review_id` | `n_papers` |
   | `ResultSummary` | `agent_run_id` (`output_summary`) | `n_findings` |
   | `ReportDocument` | `report_id` (`reports`) | `source_version`, `has_pdf` |

3. **Rules:**
   - An artifact is a **pointer** to a DB row or to a `storage_paths` file. It never holds a payload.
   - Packs may register more types. `catalog.validate()` refuses a port that names an unknown type.

## Phase 1.3 — Registration, catalog, index ✅

1. **`toolkit/tools/registry.py`:**
   - The `@tool(...)` decorator records the spec and its pack. It builds `input_model` with `pydantic.create_model` and skips a `ToolContext` parameter. `params: Model` tools keep their nested schema, so prompts do not change.
   - `ToolRegistry`: `register`, `get`, `for_agent(agent_spec, ctx)`, `for_main(ctx)`, `graph_tools()`, `by_pack()`.
2. **`toolkit/agents/registry.py`:** `AgentRegistry` with `register`, `get`, `graph_agents()`, `control_agents()` and `children_of()`.
3. **`toolkit/catalog.py`:**
   - **`build()`** discovers `agent_system.toolpacks.*` and `agent_system.agentpacks.*` with `pkgutil`, plus the entry-point groups `cubeai.toolpacks` / `cubeai.agentpacks`. The loader from Plan 3 reads the `agent.md` files. **There is no central import list.**
   - **`validate()` raises** on:
     - duplicate names, or a name used in both registries;
     - an unknown name in an allowlist, or a `harness` tool in an allowlist;
     - an unknown delegate, a delegation cycle, or a delegate that is not `internal`;
     - an `internal` agent without a `parent`;
     - a `control` agent with ports, or with `run_agent`, `plan` or `recover` in its tools. Main is the only exception: it may use `plan` and `recover`. Only `execution` agents can be graph nodes.
     - `job` tools with non-JSON args, or any manifest rule from Phase 1.1;
     - a port that names an unknown artifact type;
     - a policy hook (filled by Plan 4).
   - **`resolve(name)`** returns an `AgentSpec`, a `ToolSpec` or an error. It replaces `node_resolver`.
   - **`index(audience)`** returns compact entries for Main or the Composer (system §33): the name, the kind (agent or tool), a one-line description, the port types and the cost.
   - **`describe(name)`** returns the full schema, the ports and the manifest.

## Phase 1.4 — Dispatch, events, delegation ✅

1. **`runtime/dispatch.py` — `invoke(spec, args, ctx, cancel)`:**
   - set the contextvar and validate the args;
   - run the `before_invoke` hooks (the Guardrails plug in here in Plan 11);
   - route by `execution`: `inline` now, `job` in Plan 5, `external` in Plan 14;
   - apply `wait_for(timeout_s)`; turn exceptions into `error_result`;
   - emit start / finish / error events **once**;
   - after success, call `on_result` and send the view events through the `EventSink`.
2. **`SessionEventSink`:** calls `session.execution_monitor.on_tool_event` and `socket_store.manager` directly, with no HTTP. It is used in-process. In Plan 10 the monitor becomes the Node Runner.
3. **`agents/engine.py`:**
   - the `AgentEngine` protocol: `run(spec, task, inputs, ctx, cancel) -> AgentResult{status, output, summary, usage}`;
   - a `FakeAgentEngine` for tests. The LangGraph implementation comes in Plan 9.
4. **`delegation/run_agent.py` — the `run_agent` tool** (`pack="delegation"`, `inline`, `visibility={"agent"}`):
   - input `{agent_type, task, inputs}`; output `{status, agent_type, summary, output}`, validated against the target's `output_contract`;
   - **per-caller binding:** the `agent_type` enum contains only the caller's `delegates` (all of them `internal`), and the handler checks it again;
   - the child context gets `subagent=<type>` and `depth + 1`, capped by `MAX_AGENT_DEPTH` (default 2); a timeout applies, and cancel is passed down.
5. **`delegation/agent_as_tool.py` — `make_agent_tool(name, agent_type, output_contract, post)`:**
   - builds a `main`-visible inline tool that runs one control agent with a hard timeout and returns its validated output;
   - `post` runs a deterministic follow-up (for example, the Validator for `plan`);
   - it is used for `plan` (Plan 12) and `recover` (Plan 13).

## Phase 1.5 — Test infrastructure ✅

1. **`tests/support/toolkit_fixtures.py`:** `fake_context`, `fake_event_sink`, `fake_catalog`, `fake_domain_api` (respx/httpx mock), plus `fake_chat_model` (filled in Plan 9).
2. **`Backend/pytest.ini`:** markers `legacy_autogen`, `sandbox` and `celery`.
3. **`Backend/conftest.py`:** auto-mark these files as `legacy_autogen`:
   - `tests/unit/agents/test_*_agent.py`;
   - `tests/pipeline/test_pipeline_builder.py` and `test_pipeline_orchestration.py`;
   - the AutoGen parts of `tests/regression/agent_system/test_pipeline_failure_guard.py` and `test_segmentation_hardening.py`.
4. **`Backend/tests/run_tests.ps1`:** the deterministic suite becomes `(unit or contract or pipeline) and not legacy_autogen`.

**Tests (`tests/unit/toolkit/`):**
- every `validate()` rule, including the manifest rules;
- `AgentSpec` is not a `ToolSpec`, and agents are not in `ToolRegistry`;
- artifact compatibility; `index()` and `describe()`;
- `run_agent`: the enum is limited to delegates; a forbidden type is refused; the depth limit holds; cancel is passed down; events carry `source` / `subagent`; the output is validated;
- agent-as-tool: timeout and output validation;
- context isolation between 2 sessions;
- dispatch: timeout, error envelope, one start/finish event, `on_result` called, refusal from `before_invoke`.

**Done when:** `toolkit/` has no AutoGen or LangGraph imports (except `engines/`), and its tests pass.

## Plan 1 — as built (2026-10-01)

**Status:** done. `tests/unit/toolkit/` has 505 passing tests on Python 3.13. `test_boundary.py` enforces the "Done when" import rule.

| Phase | Commit(s) | What landed |
|---|---|---|
| 1.1 | `9fa8afc8`, `494392e8`, `db187326`, `062c1fbb` | `manifest.py`, `tools/spec.py`, `tools/consistency.py`, `ports.py`, `agents/spec.py`, `context.py`, `events.py`, `results.py` |
| 1.2 | `0c1f942e` | `artifacts/base.py`, `artifacts/builtin.py` (the 9 built-in types) |
| 1.3 | `5c7fc780`, `adea7767` | `registry.py` (generic base), `tools/registry.py` (`@tool`), `agents/registry.py`, `catalog.py` |
| 1.4 | `65586537`, `82fd5af6`, `717641c3` | `runtime/{cancel,inline,dispatch,session_sink}.py`, `agents/engine.py`, `delegation/run_agent.py`, `delegation/agent_as_tool.py` |
| 1.5 | not committed yet | `tests/support/toolkit_fixtures.py`, `tests/unit/toolkit/conftest.py`, markers, `LEGACY_AUTOGEN_TESTS` auto-marking, deterministic filter |

**Differences from the plan text** (later plans should build on these):
- **Layout:**
  - The manifest enums live in `toolkit/manifest.py`, not in `tools/spec.py`, because agents use them too.
  - The consistency rules are in `tools/consistency.py` (`check_manifest()`, `non_json_args()`).
  - `toolkit/registry.py` is a generic `Registry[T]` that the tool, agent and artifact-type registries all subclass.
- **`ToolSpec`:**
  - `context_param` names the handler's `ToolContext` argument.
  - `for_caller(spec, agent)` is a per-caller binding hook; `run_agent` uses it to narrow its `agent_type` enum.
  - The `@tool` argument model is named `{fn}Arguments`, the same as FastMCP's, so the schemas the models see do not change.
- **`ToolContext`:**
  - It has a fourth live field, `cancel` (the call's `CancelToken`), which is left out of the payload like the other live fields.
  - `for_tool()` and `for_subagent()` derive child contexts.
  - `execution_mode` defaults to `auto`, the same as the session default (Phase 11.2).
- **`ToolRegistry`:**
  - `for_main()` takes no `ctx`.
  - `screen(agent, names)` is shared by `for_agent()` and the catalog.
- **Catalog:**
  - `IndexAudience` is `main` or `composer`.
  - `describe()` reports an agent's kind as `agent_kind`.
  - `build()` skips pack packages that do not exist yet (`toolpacks`, `agentpacks`), but re-raises a broken import.
  - It reports `agent.md` files found without a loader (Plan 3 adds the loader).
  - It refuses a control agent with `delegates`, and an internal agent whose `parent` is a control agent. Control agents never get `run_agent`, so they cannot own internal agents; a control agent that needs a review step uses `post` in `make_agent_tool`.
- **Dispatch:**
  - `Dispatcher(before_invoke=…, runners={Execution.INLINE: run_inline})`. The job and external runners plug in through `runners` (Plans 5 and 14).
  - The budget is `timeout_s + queue_timeout_s`.
  - A `before_invoke` hook returns `None` to let the call through, or any value, which answers instead of the tool (a refusal or an approval request). A hook that raises refuses the call with `error_code="error"`.
- **`SessionEventSink`:** it takes `session` and `manager` as constructor arguments instead of importing `socket_store`, because of the dual import roots (`toolkit` and `agent_system.toolkit`).
- **Delegation:**
  - `run_agent` gets the agents and the engine from `ctx.session_handle`, which must satisfy the `AgentRuntime` protocol: `agents` and `engine`.
  - `make_agent_tool(..., post)` passes `post` an `ask(feedback)` function, which re-runs the agent; this is how `plan` repairs a rejected proposal (Plan 12).
- **Tests:**
  - The toolkit fixtures load from `tests/unit/toolkit/conftest.py`, not from `Backend/conftest.py`, because the root conftest also runs in the Python 3.10 MONAI image.
  - A `legacy-autogen` runner suite keeps the excluded tests runnable, and `backend-tests.yml` uses the same deterministic filter.
  - `legacy_autogen` marks the whole of `test_pipeline_failure_guard.py`, but only the `SegmentationPlannerTests` and `PipelineFailureTests` classes of `test_segmentation_hardening.py`.

**Open follow-ups:**
- Plan 0 (the 3.13 images) is still needed before the toolkit tests can run in CI.
- Pin `mcp<2` in `Backend/agent_system/requirements.txt`: `mcp>=1.17` now resolves to 2.x, which removed `mcp.server.fastmcp`.

---

# Plan 2 — Tool Packs: move every tool and make the light tools ready

**Goal:** `Backend/mcp/` is gone. All tools live in `agent_system/toolpacks/<pack>/` and are registered with `@tool` plus a full manifest. They read identity from the context, and the light tools pass the Definition of Ready.

## Phase 2.1 — The big move (one rename commit, then one edit commit)

1. **`git mv`:**

   | From (`Backend/mcp/...`) | To (`Backend/agent_system/toolpacks/...`) |
   |---|---|
   | `tools/helpers/*`, `service_urls.py` | `common/` |
   | `tools/modeling/*` | `modeling/` (moved as they are, still importing PyCaret; they cannot run after Plan 0 and are rewritten in Plan 7) |
   | `tools/feature_extraction/*` | `radiomics/` |
   | `tools/population/*`, `tools/save_population.py`, `tools/targets_matcher.py` | `population/` |
   | `medical_report/*` | `population/data/` |
   | `tools/database/**`, `tools/get_patients_info.py`, `tools/get_studies_info.py` | `db_reads/` |
   | `tools/run_segmentation.py`, `tools/check_having_mask.py`, `tools/list_models.py` | `segmentation/` |
   | `tools/latex/*`, `tools/get_modeling_results.py` | `reporting/` |
   | `tools/literature_context.py` | `literature/` |
   | `tools/scratchpad.py`, `tools/agent_output.py`, `tools/task_context.py`, `tools/browse_workspace.py` | `bookkeeping/` |
   | `test_data/` | `Backend/tests/fixtures/sample_data/` |

2. **Edit commit:**
   - replace `from . import mcp` + `@mcp.tool(...)` with `@tool(...)`, keeping every name;
   - replace `log_tool_state` / `broadcast_ws_message` with `toolpacks/common/emit.py`, a shim that forwards to `current_context().event_sink`;
   - fix the relative imports; update the spawn target string of `radiomics/features.py` `_run_extractor` and the data path in `cohort_labels.py`.
3. **Delete** what is left of `Backend/mcp/`: `server.py`, `app.py`, `tool_logging.py`, `internal_emitter.py`, `tools/__init__.py`, `tools/radiomics_analysis.py`. Also delete the dead `agent_system/tools/classification_tool.py` and its import at `modeling_agent.py:19`. This is the only edit to an AutoGen file.
4. **Tests:**
   - delete `tests/support/mcp_loader.py` and `tests/unit/mcp/test_internal_emitter.py`, because it puts `Backend/mcp` on `sys.path` and shadows the `mcp` SDK;
   - `tests/unit/functions/*` (13 files), `contracts/test_agent_output_contract.py` and `test_v1_feature_extraction_contract.py` now import `agent_system.toolpacks...` directly;
   - update the paths in the source-scan contracts: `test_v1_modeling_contract.py:15,149-182`, `test_v1_population_review_contract.py:220-245,288,331`, `test_v1_execution_spine_contract.py:298-335`, `test_v1_feature_extraction_contract.py:235-265`, `test_service_route_ownership.py:110`;
   - update `_SOURCES` in `tests/unit/services/test_event_registry.py:43-71`.

## Phase 2.2 — Common helpers

In `toolpacks/common/`:
- one shared `httpx.AsyncClient` with explicit timeouts;
- lazy, validated settings (in the style of `timeout_policy._positive_float`);
- `domain_api.session_id()` / `agent_run_id()` read `current_context()`;
- `WorkspacePath` resolves against the context at validation time;
- one `app_api_url()`, which removes 4 copies;
- remove the `sys.path` hack at `list_models.py:7-12`.

## Phase 2.3 — Context migration (all packs, including the identity reads in the heavy files)

- **Pattern A (the value is copied at import):** `agent_output.py:50-52`, `scratchpad.py:30-35`, `task_context.py:25-30`, `browse_workspace.py:3,11,66`, `search_volumes.py:10` (unused, so delete it), `regression.py:24`.
- **Pattern B (the env var is read at call time):** `targets_matcher.py:35`, `inclusion_criteria.py:18`, `validate_anatomy.py:70`, `cohort_labels.py:186,345`.
- The LLM settings in `cohort_labels.py` (`MODEL_BASE_URL`, `MODEL_NAME`, `MODEL_API_KEY`, `LLM_CONCURRENCY_LIMIT`) move to the Model Gateway `extraction` profile in Plan 9. Until then, read them from `client_config.py`.

## Phase 2.4 — Light tool fixes and manifests

1. **Fixes:** fix L1–L14 (Appendix D) and the `generate_targets` wait S12. The S12 fix uses `poll_until`, which lands in Phase 5.1; until then, write it against the same signature.
2. **A manifest on every light tool:** `pack`, `execution="inline"`, `trust="first_party"`, `isolation="inline"`, `side_effects`, `cost`, `resources`, `data_scopes`, `timeout_s` and `visibility`:

   | Group | `visibility` | Examples |
   |---|---|---|
   | Read-only reads | {`main`, `agent`} | the 14 DB tools, `get_patients_info`, `get_studies_info`, `check_having_mask`, `list_models`, `browse_workspace`, `get_modeling_results` |
   | Domain actions used inside agents | {`agent`} | population tools, `generate_targets`, `write_agent_output`, scratchpad reads and notes, `latex_*` except `latex_to_pdf` |
   | Actions that can be graph nodes | {`graph`, `agent`}, with ports | `run_segmentation_with_guard` (`cohort: Cohort` → `masks: MaskSet`), `latex_to_pdf` (`report: ReportDocument` → `report: ReportDocument`) |
   | Run bookkeeping | {`harness`} | `scratchpad_register_agent`, `scratchpad_set_status` (called by the Node Runner) |

   An LLM-backed tool such as `extract_cohort_labels` gets `cost="medium"` and `resources=["llm"]`.
3. **`run_segmentation_with_guard`:** register it in `toolpacks/segmentation/`. **Copy** the logic of `segmentation_tool()` from `segmentation_agent.py:61-134`; do not edit the AutoGen file. It gets `critical=True` and one name (L13).
4. **Literature search tools:** copy them from `literature_review_agent.py:218-520,1499-1525` into `toolpacks/literature/search.py`, **without** editing the frozen file:
   - `search_pubmed`, `search_arxiv`, `search_semantic_scholar`, `search_europe_pmc`, `score_paper_relevance`;
   - `save_literature_output`, which was `save_output`. This is the only new name: `save_output` is too generic for a global registry.

   They use the shared httpx client, `data_scopes={"network_egress"}` and `visibility={"agent"}`.
5. **`on_result` hooks** for `write_agent_output`, `generate_targets`, `get_patient`, `get_volume`, `get_modeling_results` and `latex_to_pdf`. **Copy** them from `pipeline_monitor.py:130-280`; the legacy monitor stays frozen.

## Phase 2.5 — Definition of Ready check

A smoke test runs every tool with a fake context and a mocked domain API. It checks the error envelope and the manifest rules. It also checks that no identity env var is read (by monkeypatching `os.environ`).

**Tests:**
- `tests/unit/toolpacks/light/`: one success test and one error test per tool;
- golden tests showing that each moved `on_result` hook produces the same view event as the legacy monitor;
- the literature tools against `respx` mocks.

**Done when:**
- `Backend/mcp/` no longer exists, and no `@mcp.tool` / `FastMCP` remains;
- the catalog loads **63 tools**: 62 pack tools (55 moved + `run_segmentation_with_guard` + 6 literature tools), plus `run_agent` from Plan 1;
- the DoR smoke test passes for all light tools.

---

# Plan 3 — Agent Definitions and Skills

**Goal:** every agent is an `agent.md` file that becomes an `AgentSpec`, and the skills exist. Plan 9 makes the agents run.

## Phase 3.1 — The `agent.md` format and loader

1. **The file:** `agentpacks/<pack>/<agent_type>/agent.md` has YAML front matter, and its body is the system prompt:
   ```
   ---
   agent_type: segmentation_agent
   version: 1
   kind: execution
   description: Selects and performs segmentation workflows
   model_profile: agent_default
   allowed_tools: [run_segmentation_with_guard, check_having_mask, list_models, write_agent_output, scratchpad_add_note]
   delegates: [clinical_target_intent_agent, mask_resolution_agent]
   max_tool_iterations: 12
   timeout_s: 10800
   inputs:  {cohort: Cohort}
   outputs: {masks: MaskSet}
   ui: segmentation_view
   human_in_the_loop: true
   ---
   <system prompt>
   ```
2. **Hooks:** an optional `hooks.py` next to the file exports `completion_check`, `context_builder`, `allowed_tools_fn` and the output-port `extract` functions.
3. **`toolkit/agents/loader.py`:** reads the front matter with `yaml.safe_load`, validates it with a Pydantic model, attaches the hooks and returns an `AgentSpec`. `catalog.build()` calls it for every `agent.md` file under `agentpacks/`.

## Phase 3.2 — Execution agents (6)

| Agent | Source (copy from) | Inputs | Outputs | Notes |
|---|---|---|---|---|
| `study_population_planner_agent` | `population_agent.py:128-222` | – (params: criteria text) | `cohort: Cohort` | Conditional `extract_cohort_labels` via `allowed_tools_fn`; `completion_check` = `services/population_handoff.py:34-78`, 2 retries; cohort-approval gate inside the node |
| `segmentation_agent` | `segmentation_agent.py:156-272` | `cohort: Cohort` | `masks: MaskSet` | Delegates the 2 segmentation internal agents; review gate inside the node |
| `feature_extraction_agent` | `feature_extraction_agent.py:47-132` | `cohort: Cohort`, `masks: MaskSet` | `features: FeatureTable`, `importance: FeatureImportance` (optional) | Calls the radiomics job tools |
| `result_reporter_agent` | `result_reporter_agent.py:52-106` | `modeling: ModelingOutput`, `features: FeatureTable` (optional) | `summary: ResultSummary` | Uses its real id (not `"result_agent"`) |
| `latex_reporter_agent` | `reporter_agent.py:49-105` | `summary: ResultSummary`, `literature: LiteratureContext` (optional) | `report: ReportDocument` | |
| `literature_review_agent` | `literature_review_agent.py` | – (params: question / PICO) | `literature: LiteratureContext` | Delegates the 5 literature internal agents |

**Prompt work** (L14, plus the move to the graph): copy each prompt from `system_prompts/system_messages.py`, which stays frozen, into its `agent.md`. Then:
- remove the `agent_id=` arguments, and use `scratchpad_read_agent(target_agent_id=...)`;
- remove the register / set_status instructions (the Node Runner owns them);
- use one segmentation tool name;
- read upstream data from the **"Node inputs"** section that the Node Runner provides, not by reading the scratchpad by agent id.

**There is no `modeling_agent`** (decision 6).

## Phase 3.3 — Internal agents (7, `kind: internal`, `parent` set)

- **Parent `segmentation_agent`:**
  - `clinical_target_intent_agent` (no tools);
  - `mask_resolution_agent` (`allowed_tools: [list_models]`, `max_tool_iterations: 10`).

  Both are copied from `segmentation/planner.py:251-266`.
- **Parent `literature_review_agent`:**
  - `pico_analyst`, `query_builder`;
  - `search_executor` (the 4 search tools);
  - `relevance_ranker` (`score_paper_relevance`);
  - `context_compiler` (`save_literature_output`).

  All are copied from `literature_review_agent.py:1528-1560`.

## Phase 3.4 — Control agents (3, in `agentpacks/core/`)

1. **`main_agent`:**
   - The prompt states system §1, §5 and §21: decide, never wait, answer directly when that is enough, submit work with `execute` / `graph_apply`, and use `plan` whenever it helps. **No rule forces the planner.**
   - `allowed_tools_fn` = all tools with `main` visibility. `model_profile: main`.
2. **`composer`:**
   - `output_contract` = `GraphProposal`; `allowed_tools_fn` = the read-only `main` tools, except `plan` and `recover`;
   - its prompt says it **never** applies or executes anything. `model_profile: composer`.
3. **`recovery_agent`:**
   - `output_contract` = `RecoveryProposal {diagnosis, diff | None, user_message}`;
   - the read-only `main` tools, except `plan` and `recover`. `model_profile: recovery`.

**Notes:**
- Control agents choose their tools by visibility and never list tool names. This keeps `validate()` passing before the control tools exist (Plans 12–13).
- This phase creates `planning/graph_model.py` with only the `GraphDiff`, `GraphProposal` and `RecoveryProposal` models, so that the output contracts resolve. Plan 8 fills in the rest of the graph model.

## Phase 3.5 — Skills (`agent_system/skills/*.md`)

1. **The format:** front matter with `name`, `description` and `applies_to` (a list of `main`, `composer` or agent types); the body is guidance text.
2. **Initial skills:**

   | Skill | Content copied from |
   |---|---|
   | `research_study` | The HypothesisArchitect and PipelineArchitect prompts in `planning_agents.py` (PICO; the typical radiomics flow) |
   | `modeling` | The `modeling_agent.py:36-99` prompt: choosing the target, task type, comparisons and folds, rewritten as advice on `run_modeling` params and the engine's estimator ids (Plan 7). No PyCaret names or PyCaret functions. |
   | `cohort_building` | The population agent prompt (criteria, labels, cohort size) |
   | `segmentation_choices` | The segmentation prompt (model families, review) |
   | `reporting` | The result and LaTeX reporter prompts |

3. **Rule:** a skill is guidance text. It never routes and never forces a workflow.

**Tests (`tests/unit/agentpacks/`):**
- loader errors (bad front matter, unknown port type, missing prompt);
- each spec's allowlist, iterations and ports; the conditional tool; the `completion_check` wiring;
- the skills index; `catalog.validate()` passes with all agents.

**Done when:**
- the catalog has **16 agents** (3 control, 6 execution, 7 internal) and `validate()` passes;
- every execution agent declares its ports.

---

# Plan 4 — Sandbox Foundation

**Goal:** an isolation policy, a runner interface, a hardened subprocess runner used now, and a container runner that is ready but switched off.

## Phase 4.1 — Policy

1. **`sandbox/policy.py`:**
   - `MIN_ISOLATION = {first_party: inline, third_party: container, generated_code: microvm}`;
   - heavy first-party tools declare `process`;
   - `SandboxProfile`: network (`none` \| `app_api` \| an allowlist); read-only root; rw mounts (the job dir only); `cpus`, `memory_mb`, `pids`, `timeout_s`; an env allowlist; a non-root user; a seccomp profile; optional `runtime="runsc"` (gVisor);
   - `check(spec)` (static) refuses under-isolated tools, and tools whose runner is not enabled.
2. Hook `check()` into `catalog.validate()` (the Plan 1 hook).

## Phase 4.2 — Runners

1. **`runners/base.py`:** the `Runner` protocol (`start(LaunchSpec) -> Handle`, `is_alive`, `wait`, `kill(grace_s)`), plus `select_runner(isolation)`.
2. **`runners/subprocess_runner.py` (implemented):**
   - `start_new_session=True`;
   - an **env allowlist** (no `MODEL_API_KEY`, no Redis password, no tokens);
   - `setrlimit` for memory, CPU and nproc;
   - SIGTERM → SIGKILL escalation with `killpg`;
   - stdout and stderr go to the job dir.
3. **`runners/container_runner.py`:**
   - turns a profile into `docker run` arguments: `--network`, `--read-only`, `--tmpfs /tmp`, `--memory`, `--cpus`, `--pids-limit`, `--user`, `--cap-drop ALL`, `--security-opt no-new-privileges`, seccomp, `--runtime runsc`;
   - starting a container is gated by `SANDBOX_CONTAINER_ENABLED` (default `0`).
4. **`runners/microvm_runner.py`:** a stub (`NotImplementedError`) with notes on E2B / Firecracker.

## Phase 4.3 — Runtime check for the Guardrails

`sandbox/policy.runtime_check(spec) -> (outcome, code)` confirms, at call time, that the needed runner is enabled and that the profile is valid. Plan 11 uses it as a guardrail rule: `permission_denied` when the tool is under-isolated, `resource_unavailable` when its runner is disabled.

## Phase 4.4 — Threat model

`cubeai-docs/security/sandbox.md` covers:
- the trust levels and data scopes;
- why the Docker socket is root-equivalent, so enabling containers needs rootless Docker/Podman or a separate launcher;
- the egress rules for patient data.

**Tests (`tests/unit/sandbox/`):**
- the policy matrix and the `validate()` refusals; `runtime_check` outcomes;
- profile → docker args (golden test); rlimits applied; env allowlist (no secrets reach the child);
- kill escalation reaches grandchildren; container start refused while disabled;
- (`-m docker`) a real container run.

**Done when:** `validate()` enforces the policy, and the subprocess runner passes all its tests.

---

# Plan 5 — Job Platform (Celery + Redis)

**Goal:** heavy tools run as Celery jobs on Redis behind one `JobClient` contract. Jobs have deterministic ids, real cancel, progress, timeouts, retention, restart recovery, OOM detection and queue liveness.

## Phase 5.1 — Job contract and client library (`Backend/jobs_core/`)

1. **`contract.py`:**
   ```
   JobRequest {
     job_id:  sha256(agent_run_id | job_name | canonical_json(args))   # agent_run_id = the node run (Plan 6) -> resume re-attaches
     attempt, job_name (a REGISTERED tool name, never an import path), job_version,
     args (JSON only), context (ToolContext.to_payload()),
     resource_class (derived from resources), isolation (process|container),
     timeout_s, queue_timeout_s, priority
   }
   JobRecord { id, status, error_code, attempt, input_hash, progress, heartbeat_at, queued_at,
               started_at, finished_at, error, result | result_ref, host, pgid }
   ```
   `error_code` is one of: `error`, `timeout`, `queue_timeout`, `oom`, `interrupted`, `cancelled`, `run_not_running`.
2. **`status.py`:** `QUEUED RUNNING CANCELLING SUCCEEDED FAILED TIMED_OUT CANCELLED INTERRUPTED`, plus `TERMINAL`, `ACTIVE` and `is_final()` (an unknown status counts as final).
3. **`client.py`:** the `JobClient` protocol: `submit` (the same id returns the existing record), `get`, `wait(budgets, on_progress, cancel)`, `result`, `cancel`, `purge`, `list(run_id, active_only)`.
4. **`polling.py`:** `RetryPolicy` (backoff, jitter, cap, `Retry-After`) and `poll_until()` with a monotonic, pause-aware deadline.
5. **`budgets.py`:** `JobBudgets(queue_s, run_s, result_s, unavailable_s)`, where the outer budget = the sum of the inner budgets + a margin.
6. **`backends/in_memory.py`** for tests.

## Phase 5.2 — Redis backend (client side)

`backends/celery_redis.py`:
- uses `redis.asyncio`; the record is in `cubeai:job:{id}` (a hash) and the index in `cubeai:jobs:run:{agent_run_id}`;
- dedupes with `SET NX`; the same id with a different `input_hash` → a conflict error;
- `send_task("compute.run_tool_job", task_id=f"{id}.{attempt}", queue=f"compute.{resource_class}", expires=queue_timeout_s)`;
- re-queues terminal non-success jobs when `retry=True` (`attempt + 1`);
- `cancel` = a Redis cancel flag + `revoke(terminate=True)`;
- sets a TTL on terminal records (`JOB_RETENTION_HOURS`, default 24).

## Phase 5.3 — Celery worker (`Backend/compute_worker/`)

1. **`celery_app.py`:**
   - JSON only (`accept_content=["json"]`);
   - `task_acks_late=True`, `task_reject_on_worker_lost=False`;
   - `worker_prefetch_multiplier=1`;
   - `broker_transport_options={"visibility_timeout": MAX_JOB_SECONDS + 600}` (otherwise Redis re-delivers long jobs);
   - `task_ignore_result=True` (our Redis record is the source of truth);
   - soft and hard time limits taken from the request;
   - queues: `compute.cpu` (started); `compute.gpu` / `compute.highmem` (declared only).
2. **`tasks.py`:** one task, `compute.run_tool_job(request)`.
3. **`supervisor.py`:**
   1. Check the cancel flag, the queue timeout, and that the run is still `running` (`GET /agent-runs/{id}`; H20).
   2. Mark the job `RUNNING` with its host and pgid.
   3. Pick a runner with `select_runner(isolation)` and launch the child.
   4. Loop: relay `progress.json` to the record, refresh the heartbeat, watch the cancel flag, and enforce the timeout with `killpg`.
   5. On exit: read `result.json`. Store the result in Redis if it is ≤ `JOB_RESULT_MAX_BYTES`; otherwise keep a file ref in the job dir. The final status is `SUCCEEDED`, `FAILED`, `TIMED_OUT` or `CANCELLED`.
   6. **OOM detection:** a child that reports `MemoryError`, or that dies by SIGKILL / exit code 137 near its memory limit, is recorded as `FAILED` with `error_code="oom"`.
4. **`child.py`:**
   - calls `ensure_storage_layout()` and `install_write_guard()`, and sets up a per-job log handler;
   - applies thread caps (`OMP_NUM_THREADS`, `COMPUTE_CPU_THREADS`);
   - builds `ToolContext.from_payload()` with a `FileProgressSink`;
   - looks up `job_name` in the catalog (an **allowlist**: only `execution="job"` tools);
   - runs the **same tool handler**, and always writes a result, even on error.
5. **`beat.py`:**
   - a stale heartbeat → `INTERRUPTED`; TTL cleanup of job dirs;
   - a `worker_ready` hook kills process groups left over on this host and marks their jobs `INTERRUPTED`.
6. **Queue liveness:** each worker refreshes `cubeai:queue:{queue}:alive` (TTL 30 s) on `worker_ready` and on a timer. Plan 11 (resource availability) and Plan 13 (resource failures) read it.
7. **`Backend/storage_paths.py`:** add `COMPUTE_JOBS_DIR` (and document it in `Backend/storage/README.md`).

## Phase 5.4 — Toolkit integration

`toolkit/runtime/job.py`:
- builds the `JobRequest` from the spec and the context;
- runs `JobClient.submit` → `wait` (progress goes to the `EventSink` as `tool_progress`) → `result`, which returns the same JSON as today;
- maps the cancel token to `JobClient.cancel`, and calls `purge` in `finally`;
- `dispatch.py` routes `execution="job"` here.

## Phase 5.5 — Deploy and CI

1. **Requirements:**
   - agents image: `celery[redis]>=5.4`, `redis>=5`;
   - `Backend/requirements/test.txt`: `fakeredis`;
   - remove `fastmcp`; keep `mcp` (the client, for Plan 14);
   - `Backend/agent_system/requirements.txt` is **UTF-16 LE with CRLF**, so keep that encoding.
2. **`deploy/docker-compose.backend.gpu.yml`:**
   - `redis` (`redis:7-alpine`, `--appendonly yes --requirepass ${REDIS_PASSWORD}`, volume `redis-data`, internal network);
   - `compute-worker-cpu`: the agents image, running `celery -A compute_worker.celery_app worker -Q compute.cpu -c ${COMPUTE_MAX_CONCURRENCY:-1}`. It uses the **same `/storage` mount path**, a non-root user and no Docker socket. Its network reaches only the Application API and Redis.
   - `compute-beat`;
   - remove the `Backend/mcp` bind mount (`:13`).
3. **Overlays:** the dev overlay adds `flower` and a published Redis port; the prod overlay publishes nothing.
4. **`deploy/Dockerfile.agents`:** copy `jobs_core`, `compute_worker` and `sandbox`; remove `COPY ./Backend/mcp` (`:40`).
5. **`deploy/.env.example`:** `REDIS_PASSWORD`, `REDIS_URL`, `COMPUTE_MAX_CONCURRENCY`, `COMPUTE_CPU_THREADS`, `JOB_RETENTION_HOURS`, `JOB_HEARTBEAT_TIMEOUT_S`, `JOB_RESULT_MAX_BYTES`.
6. **CI:** a Redis service for the `celery` integration job in `backend-tests.yml`; the new packages in `docker-build-agents.yml`.

**Tests:**
- **`tests/unit/jobs/`** (fakeredis + in-memory):
  - job id and input hash; dedupe; conflict on different args;
  - re-queue after each terminal state; `expires` → `TIMED_OUT(queue)`;
  - retry policy / `Retry-After`; pause-aware deadline;
  - supervisor with a fake runner; result size → file ref; OOM mapping; liveness key refresh.
- **`tests/integration/jobs/`** (`-m "integration and celery"`, with real Redis and a real worker):
  - submit → progress → result;
  - cancel kills the process group (a fake handler that sleeps and starts a child);
  - soft and hard timeouts;
  - worker restart → `INTERRUPTED`; the stale-heartbeat sweep;
  - `run_not_running`; the write guard is active in the child.

**Done when:** the unit and integration job suites pass, and `runtime/job.py` works with both the in-memory and the Celery backends.

---

# Plan 6 — Session State Store (Application API)

**Goal:** durable state for Graphs, Nodes, Runs, Artifacts and Approvals (system §4 "State"), owned by the Application API. The Agent System reaches it only over HTTP.

## Phase 6.1 — Schema (Alembic `o1f0a2c3d4e5_v1_o_session_graph.py`, `down_revision = n1f0a2c3d4e5`)

1. **`sessions`:**
   - add `execution_mode` (`plan|auto`, default `auto`; existing rows also get `auto`), `privacy_mode` (`standard|phi_local`, default `standard`) and `context_summary` (text);
   - extend the status CHECK with `idle`, `awaiting_approval` and `blocked`.
2. **`session_graphs`:** `session_id` (PK/FK), `current_version` (int, default 0), `updated_at`.
3. **`graph_versions`:** `(session_id, version)` PK, `base_version`, `author` (`main|composer|recovery|user|harness`), `status` (`proposed|applied|rejected|superseded`), `diff` (JSONB), `summary`, `created_at`, `applied_at`.
4. **`graph_nodes`:**
   - `node_id` (PK, readable, e.g. `seg_42`, allocated from a per-session sequence), `session_id`, `kind` (`agent|tool`), `ref_name`, `ref_version`;
   - `params`, `bindings` (`{port: {node_id, port} | {artifact_id}}`), `after` (control dependencies) and `expectations`, all JSONB;
   - `status`: `pending|ready|awaiting_approval|queued|running|waiting_for_user|done|failed|cancelled|invalidated|held|removed`;
   - `hold_reason`, `fingerprint`, `current_run_id` (FK `agent_runs`), `cache_source_run_id`, `added_in_version`, `removed_in_version`.
5. **`agent_runs`, generalized to hold every node run:**
   - `step_id` becomes nullable (legacy rows keep it);
   - add `node_id` (FK), `attempt`, `producer_kind` (`agent|tool`), `producer_name`, `fingerprint`, `job_id`, `usage` (JSONB tokens/cost) and `heartbeat_at`, with a unique `(node_id, attempt)`;
   - the status set gains `cancelled` (from `pending` or `running`);
   - the "compiled prompt before start" check applies only when `producer_kind='agent'`;
   - update the transition trigger (`f1f0a2c3d4e5...:100-108`) to match.
6. **`graph_artifacts`:** `artifact_id`, `session_id`, `type`, `type_version`, `produced_by_run_id` (FK `agent_runs`), `node_id`, `port`, `ref` (JSONB), `summary` (JSONB), `content_hash`, `phi`, `superseded_at`.

   It is a separate table from the existing `artifacts` (figures and exports), which stays as it is.
7. **`approvals`:** `approval_id`, `session_id`, `subject_type` (`graph_version|node|tool_call`), `subject_ref`, `reason` (`plan_mode` or `guardrail:<code>`), `status` (`pending|approved|rejected|expired|superseded`), `payload`, `requested_at`, `decided_at`, `decision_comment`.
8. **Producer rules** (checked by capability, not by step): `require_producer_run(db, run_id, allowed_producers)` checks `agent_runs.producer_name` (falling back to `step.agent_type` for legacy rows) and `status='running'`:

   | Table | Allowed producers |
   |---|---|
   | `model_results` | `run_modeling`, the 5 modeling tools (`train_classifier`, `train_regressor`, `run_classifier_inference`, `run_regression_inference`, `refit_model`; Plan 7), legacy `modeling_agent` |
   | `radiomics_features`, `feature_sets` | `feature_extraction_agent`, `radiomics_extract_features` |

   Update the `domain_rows_enforce_producer` trigger (`i1f0a2c3d4e5...:51-75`) in the same way.

## Phase 6.2 — Models, schemas, repositories

Follow the Application API layering:
- one file per entity in `db/models/`, `schemas/` and `repositories/`: `session_graph.py`, `graph_nodes.py`, `graph_artifacts.py`, `approvals.py`;
- commit inside `try`, and roll back on `SQLAlchemyError`;
- return `(success, error)`.

Key operations:
- **`apply_version(session_id, base_version, author, diff, node_upserts, run_changes)`:** one transaction inside `services/publication.py` `publication()`. It locks `session_graphs` `FOR UPDATE`. If `current_version != base_version`, it returns `(False, "STALE_BASE:<current>")`. Otherwise it writes the upserts, increments the version and stages the durable events.
- **The rest:** `propose_version` (status `proposed` + an approval row), `decide_approval`, `create_node_run`, `cancel_run`, `heartbeat_run`, `record_artifacts` / `supersede_artifacts`, `list_active_runs(session_id?)`, and `set_session_settings`.
- **Transitions:** update `_ALLOWED_TRANSITIONS` (`repositories/sessions.py:109-143`) for `idle`, `awaiting_approval` and `blocked`.

## Phase 6.3 — Routes (thin; registered in `application_api/main.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/graphs/session/{id}` | Current version, nodes, current artifacts |
| POST | `/graphs/session/{id}/versions` | Apply a version (CAS); 409 `STALE_BASE` |
| POST | `/graphs/session/{id}/proposals` | Store a proposed version + an approval |
| PATCH | `/graph-nodes/{node_id}` | Status / hold / current run |
| POST | `/agent-runs/` | Create a node run (`node_id`, `attempt`, `producer_kind`, `producer_name`, `fingerprint`) |
| POST | `/agent-runs/{id}/cancel`, `/agent-runs/{id}/heartbeat` | New run transitions |
| POST / GET | `/graph-artifacts`, `/graph-artifacts/session/{id}` | Record and list artifacts |
| GET / POST | `/approvals/session/{id}`, `/approvals/{id}/decision` | Pending approvals; decide |
| PATCH | `/sessions/{id}/settings` | `execution_mode`, `privacy_mode` |
| PUT | `/sessions/{id}/context-summary` | Main's rolling summary |

## Phase 6.4 — Agent System client

1. **`clients/backend_client.py`:** add one method per route above, plus `add_chat_message` / `list_chat_messages` for the Context Manager.
2. **`execution/graph_store.py`:** a typed facade (`GraphStore`) over those methods, plus `InMemoryGraphStore` for the tests of Plans 8–13.

**Tests:**
- **Contract:** extend `tests/contracts/test_v1_schema_contract.py` (new tables, CHECKs, FKs); add a new contract test showing that every producer in the allowlists exists in the catalog (Plans 2 and 7).
- **Unit:** route validation with fake repositories; update `tests/unit/services/test_session_status_transitions.py` and `test_agent_type_validation.py`.
- **Integration** (`-m "integration and docker"`, against the compose Postgres):
  - the migration runs up and down;
  - of two concurrent applies on the same base, one wins and the other gets `STALE_BASE`;
  - a tool node run starts without a prompt; the `cancelled` transitions work;
  - the producer trigger accepts `run_modeling` and refuses a wrong producer.

**Done when:** the migration applies, and all the tests above pass.

---

# Plan 7 — Modeling Engine, Heavy Tools as Jobs + `run_modeling`

**Goal:** PyCaret is replaced by a scikit-learn modeling engine. The 7 heavy tools and the new `run_modeling` tool run through Celery. They are safe to run twice and at the same time, report progress, and can be used as graph nodes.

## Phase 7.0 — Modeling engine without PyCaret (`toolpacks/modeling/engine/`)

The engine replaces every PyCaret call the tools make today (`setup`, `compare_models`, `create_model`, `tune_model`, `blend_models`, `predict_model`, `pull`, `plot_model`, `interpret_model`, `get_config`, `save_model`, `load_model`). It is plain functions over pandas and scikit-learn, and it has no tool or HTTP code.

1. **`estimators.py`:** one registry for classification and one for regression.
   - The keys are the short model ids that PyCaret used and that `model_results.parameters.pycaret_model_id` already stores (`lr`, `ridge`, `knn`, `nb`, `dt`, `rf`, `et`, `ada`, `gbc` / `gbr`, `svm`, `lda`, `mlp`, `lightgbm`, `xgboost`, `dummy`, …), so old records and prompts keep their meaning.
   - Each entry holds: the class path, default params, a tuning search space, and whether it gives probabilities and feature importances.
   - There is no CatBoost entry.
2. **`preprocessing.py`:** replaces `setup()`.
   - A scikit-learn `Pipeline`: imputation (`SimpleImputer`, median for numbers and most-frequent for categories), optional scaling (`StandardScaler`), and one-hot encoding for categorical columns, then the estimator.
   - `model_results.preprocessing` records these explicit settings, not PyCaret setup arguments.
3. **`cv.py`:**
   - `StratifiedKFold` for classification and `KFold` for regression, seeded;
   - the fold count is clamped to the smallest class count (the classifier check that H8 adds to the regressor);
   - the existing selection-aware holdout (`holdout.py`, `selection_split.py`) is reused unchanged.
4. **`compare.py`, `tune.py`, `blend.py`:**
   - compare = `cross_validate` for each requested estimator → a metrics table sorted by the chosen metric → the top `n`;
   - tune = `RandomizedSearchCV` over the registry's search space (seeded, fixed `n_iter`);
   - blend = a soft `VotingClassifier` / a `VotingRegressor` of the tuned top models. Blends are recorded with `parameters.members`, as today.
5. **`metrics.py`:** the metric names stay the ones in today's result tables, so the success result shape and `ModelingView` do not change:
   - classification: `Accuracy`, `AUC`, `Recall`, `Prec.`, `F1`, `Kappa`, `MCC`;
   - regression: `MAE`, `MSE`, `RMSE`, `R2`, `RMSLE`, `MAPE`.
6. **`plots.py`:**
   - matplotlib with the `Agg` backend, one per-job plots dir (H14);
   - classification: ROC/AUC, confusion matrix, feature importance;
   - regression: residuals, prediction error, feature importance;
   - SHAP summary plot through `shap` for tree models, which replaces `interpret_model`.
7. **`persistence.py`:**
   - `joblib.dump` of the fitted `Pipeline` to a temp file, then an atomic rename (H1); the digest goes into `model_file_digest`;
   - `model_results.environment` records `engine: "sklearn"` and the library versions.
8. **Rules:**
   - `n_jobs` is capped by `COMPUTE_CPU_THREADS`;
   - nothing prints (H7);
   - no `os.chdir` (H5); no global logging changes (H6).

## Phase 7.1 — Rename, rewrite and register as job tools

1. **The 5 modeling tools are rewritten on the engine and renamed.** Their input params and success result shapes stay the same; only the name changes.

   | Old name | New name |
   |---|---|
   | `pycaret_train_classifier` | `train_classifier` |
   | `pycaret_train_regressor` | `train_regressor` |
   | `pycaret_run_inference` | `run_classifier_inference` |
   | `pycaret_run_regression_inference` | `run_regression_inference` |
   | `pycaret_refit_model` | `refit_model` |

   The old names appear nowhere in new code. They survive only in old durable events and in the frozen AutoGen files, which Plan 17 deletes.
2. **Base manifest for all 7 heavy tools:**
   - `execution="job"`, `side_effects="compute"`, `cost="heavy"`, `resources=["cpu_heavy"]`;
   - `trust="first_party"`, `isolation="process"`.

| Tool | `visibility` | Ports |
|---|---|---|
| `train_classifier`, `train_regressor` | {`graph`} | `features: FeatureTable` → `modeling: ModelingOutput` |
| `run_classifier_inference`, `run_regression_inference`, `refit_model` | {`graph`} | `modeling: ModelingOutput` (+ `features`) → `modeling: ModelingOutput` |
| `radiomics_extract_features` | {`agent`, `graph`} | `cohort: Cohort`, `masks: MaskSet` → `features: FeatureTable` |
| `radiomics_analyze_importance` | {`agent`, `graph`} | `features: FeatureTable` → `importance: FeatureImportance` |

The worker runs the same handlers, so no wrapper code is needed.

## Phase 7.2 — Models trained with PyCaret (legacy records)

1. **Existing `model_results` rows stay readable:** metrics, predictions and figures. Nothing is migrated or deleted.
2. **Their saved `.pkl` files are PyCaret pipelines**, which cannot be loaded without PyCaret.
   - The inference tools treat a record without `environment.engine == "sklearn"` as legacy;
   - they return `error_result("legacy_model_format", ...)` and name `refit_model` in the message.
3. **`refit_model` rebuilds a legacy variant from its record** (the same checks as `refit_core.refit_refusal` today):
   - the estimator from `parameters.estimator` (a scikit-learn, LightGBM or XGBoost class path loads without PyCaret; a `pycaret.*` class path is refused with a clear error);
   - the preprocessing mapped from the recorded PyCaret setup arguments to the engine pipeline;
   - the same split definition and seed.

   It writes a **new** variant with `environment.refit_of = <old model_result_id>`, because its numbers can differ slightly from the original PyCaret fit.

## Phase 7.3 — `run_modeling` (`toolpacks/modeling/run_modeling.py`)

1. **Manifest (system §15):**
   - name `run_modeling`, version 1;
   - input `features: FeatureTable` (many), filling the param `feature_set_ids: list[str]`; output `result: ModelingOutput`;
   - `side_effects: compute`, `cost: medium`, exactly as in system §15. The manifest rules allow a `medium` job tool: only `heavy` forces `execution="job"`, and the rules do not work the other way round.
   - `resources: [cpu_heavy]`, `phi: false`, `ui: modeling_results_view`;
   - `execution: job`, `visibility: {graph}`.
2. **Params:**
   - `feature_set_ids`, `target`, `task` (`classification|regression|auto`), `comparisons`, `folds`, `seed`, `include_inference`;
   - `auto` uses one fixed, documented rule based on the target column type. This is a technical default, not a strategy choice.
3. **Handler:**
   - calls the modeling engine (Phase 7.0) in the same child process, in the order train → inference/evaluation → select;
   - writes `model_results` with producer `run_modeling`;
   - returns a result from which the `result` port extracts `ModelingOutput`.
4. **UI and progress:**
   - `on_result` emits the `modeling_output` view event, copied from `pipeline_monitor.py:182-252`, so `ModelingView` keeps working;
   - progress goes through the context sink.

## Phase 7.4 — Correctness for jobs

Fix H1–H18 (Appendix C). Two of them change code outside the tools:
- H12 changes `application_api/repositories/radiomics.py`, so that reused rows are linked to the current run.
- H13 changes `toolpacks/bookkeeping/agent_output.py`: skip the temp cleanup while `JobClient.list(run_id, active_only=True)` is not empty.

## Phase 7.5 — Progress and hooks

- Progress calls go to the context sink. Inside the job, this is the file sink, which the supervisor relays (H19).
- The `on_result` hooks for radiomics and modeling are **copied** from `pipeline_monitor.py:182-252`. The legacy monitor matches on `tool_name == "pycaret_train_classifier"` (`:227`); the copied hook lives on the tool spec, so it does not depend on the name.

**Tests (`tests/unit/toolpacks/heavy/`, `tests/unit/toolpacks/modeling/engine/`):**
- **engine** (synthetic data only):
  - the same seed gives the same metrics table and the same selected model;
  - the metrics table columns equal today's names (a golden test on a recorded result shape, e.g. `tests/manual/view_payload_harness.py`);
  - fold clamp; compare → tune → blend order; a blend records its members;
  - `n_jobs` is capped by `COMPUTE_CPU_THREADS`; no stdout output;
- **legacy records:** inference on a record without `engine: "sklearn"` returns `legacy_model_format`; `refit_model` maps a recorded PyCaret setup to the engine pipeline, refuses a `pycaret.*` class path, and writes a new variant with `refit_of`. This replaces `tests/unit/functions/test_model_refit.py:80`, which needs PyCaret;
- **source scan:** nothing under `toolpacks/` imports `pycaret`, and no new tool name starts with `pycaret_`;
- H15/H16/H17 unit tests;
- model path + atomic rename (H1); training from bytes (H2); exact model match (H4);
- per-job YAML and plots dirs (H9, H14); no global logging changes (H6);
- `run_modeling`: the `auto` rule, the order of steps, and the producer name sent;
- the `on_result` golden tests;
- (`-m "integration and celery"`) each tool, and `run_modeling`, runs in `child.py` on a tiny synthetic CSV or a tiny synthetic NIfTI.

**Done when:** all 8 tools run through the job runtime on the scikit-learn engine, nothing imports `pycaret`, and the heavy and engine tests pass.

---

# Plan 8 — Validator and Diff Engine

**Goal:** deterministic graph code: a graph model, a Validator that returns only technical errors, fingerprints, the affected set, and atomic, versioned diff application (system §9, §26, §27).

## Phase 8.1 — Graph model (`planning/graph_model.py`, pure Pydantic)

1. **The model:**
   - `NodeDef(node_id | local_ref, kind, ref, params, bindings, after, expect)`;
   - `Binding(from_node | artifact_id, port)`;
   - `Expectation(metric, op, value)`, where `op` is one of `== != < <= > >=`;
   - `GraphState(version, nodes)`.
2. **`GraphDiff(base_version, ops, on_running = "reject" | "restart")`.** The ops are:
   - `AddNode`, `RemoveNode`, `UpdateParams`, `Rebind`, `AddAfter`;
   - `SetExpectations`, `ReleaseHold`, `RerunNode`.
3. **`GraphProposal(proposal_id, diff, rationale, errors)`:** new nodes use local refs (`$1`, `$2`). The store turns them into real ids when the diff is applied.

## Phase 8.2 — Validator (`planning/validator.py`)

`validate(state, diff, catalog) -> list[ValidationError(code, node, detail)]`. It never suggests a strategy (system §9).

| Code | When |
|---|---|
| `UNKNOWN_REF` | `ref` is not in the catalog |
| `NOT_GRAPH_VISIBLE` | The tool or agent cannot be a node (not `graph` / not `execution`) |
| `INVALID_PARAMS` | The params fail `input_model` |
| `MISSING_INPUT` | A required input port is not bound |
| `TYPE_MISMATCH` | A bound artifact type is not compatible (e.g. "expected MaskSet, received ImageSet") |
| `UNKNOWN_PORT` | The port does not exist on the upstream or target node |
| `CYCLE_DETECTED` | The bindings plus `after` form a cycle |
| `DANGLING_REF` | A binding or `after` names a missing or removed node |
| `DUPLICATE_NODE` | The same id is used twice |
| `BAD_EXPECTATION` | The metric is not in the output type's summary model |
| `UNKNOWN_RESOURCE` | A declared resource pool is not in `config/resources.yaml` |
| `REMOVE_WITH_DEPENDENTS` | A removed node still has bound dependents |
| `RUNNING_NODE_AFFECTED` | The diff affects a running node and `on_running="reject"` |
| `STALE_BASE` | `base_version` is not current (returned on apply) |

## Phase 8.3 — Fingerprints and affected set (`execution/fingerprint.py`)

1. **The fingerprint:** `fingerprint(node) = sha256(kind | ref | ref_version | canonical_json(params) | sorted((port, fingerprint(upstream), upstream_port)))`.
   - It is structural, so it can be computed before anything runs.
   - A binding to an existing artifact uses that artifact's `content_hash` instead.
2. **Artifact hash:** `content_hash = sha256(producer fingerprint | port)`.
3. **The affected set:** `affected(old, new)` = the nodes whose fingerprint changed. This spreads to all downstream nodes automatically, because the formula chains fingerprints. This gives system §26: in A→B that becomes A→New→B, B is affected.

## Phase 8.4 — Diff Engine (`execution/diff_engine.py`)

1. **`plan_diff(state, diff) -> DiffOutcome`** has the fields `new_state`, `status_changes`, `runs_to_cancel`, `artifacts_to_supersede` and `errors`. It applies the §27 rules:

   | Node state | Unaffected | Affected |
   |---|---|---|
   | `pending` / `ready` | stays | its params and fingerprint are updated |
   | `running` | continues | `on_running="restart"`: cancel, then re-queue as a new attempt. Otherwise: `RUNNING_NODE_AFFECTED` |
   | `done` | its output stays cached | `invalidated`: the artifacts are superseded (never deleted), and the node runs again when ready |

2. **`apply(diff, author)`:**
   - runs the Validator and then `plan_diff`;
   - calls `GraphStore.apply_version` (CAS on `base_version`);
   - on success, emits `graph_version_applied` and `node_status` events, and wakes the Executor (`graph_changed`).
3. **`propose(diff, author)`:** stores a `proposed` version, used by Plan mode (Plan 11).

**Tests (`tests/unit/planning/`, `tests/unit/execution/test_diff_engine.py`):**
- every Validator code;
- the §27 matrix (3 states × affected/unaffected × `on_running`);
- the A→New→B example; fingerprint stability (the same diff gives the same fingerprint);
- a stale base; local-ref resolution;
- artifacts are superseded, never deleted.

**Done when:** all the Validator and Diff Engine tests pass with `InMemoryGraphStore`.

---

# Plan 9 — LangGraph Engine and Model Gateway

**Goal:** real LLM execution for every agent through LangGraph, with model selection, fallback and locality handled by one gateway.

## Phase 9.1 — Dependencies

- Add `langgraph`, `langchain-core` and `langchain-openai` (pin the exact versions when implementing). They go into `Backend/agent_system/requirements.txt` (UTF-16 LE, CRLF) and `deploy/Dockerfile.agents`.
- No LangGraph checkpoint packages are needed (decision 7).

## Phase 9.2 — Model Gateway (`harness/model_gateway.py`, `config/models.yaml`)

1. **Profiles:** `main`, `composer`, `recovery`, `agent_default`, `extraction` (cohort labels) and `summarizer`. Each profile has a primary model and a list of fallbacks.
2. **A model entry:** `provider` (`openai|azure_openai|ollama`), `name`, `base_url_env`, `api_key_env`, `locality` (`local|external`), `capabilities` (`tools`, `json`) and `max_tokens`.
3. **`get_chat_model(profile, ctx) -> BaseChatModel`:**
   - builds `ChatOpenAI` (for OpenAI, and for Ollama through its OpenAI-compatible URL) or `AzureChatOpenAI`;
   - adds `.with_fallbacks(...)`;
   - retries with the delays from `LLM_RETRY_DELAYS_SECONDS`, reusing `client_config._retry_delays`.
4. **User settings:** the existing `/client-config` settings (`ClientSettingsPanel`) still override the default entry.
5. **`locality_filter(ctx)` hook:** empty now; Plan 13 fills it for PHI-Local.
6. **Usage:** a usage callback writes token counts to `agent_runs.usage` through the run's context. The budgets in Plan 11 use them.

## Phase 9.3 — LangGraph engine (`toolkit/engines/langgraph_engine.py`)

`LangGraphAgentEngine` implements `AgentEngine`. It builds a custom `StateGraph`, not the prebuilt agent, so that every tool call goes through our dispatch.

1. **State:** `{messages, iterations, completion_retries, output}`.
2. **Nodes:**
   - **`gate`:** waits while the session is paused, and adds any mailbox messages (user interruptions) as human messages;
   - **`model`:** the gateway model bound to the tool schemas;
   - **`tools`:** calls `dispatch.invoke` for each tool call (in parallel only when every called tool is `read_only`);
   - **`check`:** runs `completion_check` / parses `output_contract`. On failure, it adds a correction message while `completion_retries < max_completion_retries`.
3. **Edges:** `gate → model`; `model →` `tools` when there are tool calls, otherwise `check`; `tools → gate`; `check → END` or `gate`.
4. **Limits:** `recursion_limit` is derived from `max_tool_iterations`. Reaching the cap ends with `error_code="max_iterations"`.
5. **Tool schemas:** `StructuredTool.from_function(coroutine=..., args_schema=spec.input_model)`. External tools use their raw JSON schema.
6. **Structured output** (for the composer and recovery agents): a final `submit_output` tool whose schema is the `output_contract`.
7. **Cancel:** cancelling the run task raises `CancelledError`, and the cancel token reaches `dispatch.invoke`, so a running job is cancelled (Plan 5).
8. **Streaming:** the Main Agent streams `astream(stream_mode="messages")` as ephemeral `stream_chunk` events.
9. **No checkpointer:** each `run()` is one node run or one Main turn. The history comes from the Context Manager (Plan 12). LangGraph `interrupt()` is not used, because approvals are handled by the harness.

## Phase 9.4 — Engine consumers inside tools

1. **The segmentation module:**
   - `module.py:96-133` (which builds AutoGen agents from the MCP workbench) and `:1375-1376` now call the engine: `AgentEngine.run("clinical_target_intent_agent" | "mask_resolution_agent", ...)`;
   - the calls go through the `run_agent` core, so depth, events and cancel work;
   - `planner.py` keeps its prompts and parsing, and loses its `AssistantAgent` factories.
2. **`cohort_labels.py`:** its LLM calls use the gateway `extraction` profile.

**Tests (`tests/unit/engines/`)** use `langchain_core` fake chat models (`GenericFakeChatModel` / `FakeMessagesListChatModel` with scripted tool calls), with no network:
- the tool-call path through dispatch (events, error envelope);
- the iteration cap; completion retries; `output_contract` parsing;
- the pause gate; mailbox injection;
- cancel stops a running tool;
- fallback on a model error; usage recorded;
- the segmentation module calls the internal agents through the engine.

**Done when:** every `AgentSpec` can run on `LangGraphAgentEngine` with a fake model, and `FakeAgentEngine` still passes the Plan 1 tests.

---

# Plan 10 — Executor, Node Runner, Watchdog

**Goal:** the only component that runs work (system §11-§14, §24). It handles readiness, dependencies, resources, runs, monitoring, completion, the cache and unlocking downstream nodes. It absorbs `RunHarness` and `DirectToolExecutor` from tools_plan.

## Phase 10.1 — Node Runner (`execution/node_runner.py`)

`run(node, attempt)`:
1. `catalog.resolve(node.ref)`. Create the run row (`POST /agent-runs/` with `producer_kind`, `producer_name` and `fingerprint`), then start it.
2. **Resolve the inputs:** load the bound artifacts. For tools, map `port.param` → the handler args. For agents, render a **"Node inputs"** section (the artifact summaries + refs) with `context_builder`.
3. **Agent Node:**
   - call `scratchpad_register_agent` (a harness tool);
   - `engine.run(spec, task=node.params.task, inputs, ctx, cancel)`;
   - run `completion_check` (default: the agent output exists with a valid status).
4. **Tool Node:** `dispatch.invoke(spec, params + input refs, ctx, cancel)`, which is inline or a job.
5. **Outputs:** call each output port's `extract`, then `record_artifacts` (with the `phi` label taken from the spec or type).
6. **Finish:**
   - on success, complete the run and set the node to `done`;
   - on failure, fail the run with an `error_code` (`error|timeout|oom|interrupted|cancelled|max_iterations|resource_unavailable`) and set the node to `failed`.
7. **Events and heartbeat:**
   - a durable `node_status` event on every change;
   - `pipeline_agent_status` is still emitted for agent nodes, as today;
   - the `on_result` view events come from dispatch;
   - a heartbeat every `NODE_HEARTBEAT_S`.
8. **In-node user gates** (cohort approval, segmentation review): when the tool emits `agent_waiting_for_user_input`, the node shows `waiting_for_user`; `agent_waiting_resolved` sets it back to `running`.

## Phase 10.2 — Executor loop (`execution/executor.py`)

There is one `SessionExecutor` task per session with work. It follows system §24:

```
while graph.has_unfinished():
    for node in graph.ready():                  # deps done, not held, no pending approval, not paused
        decision = guardrails.check(NodeStart(node))       # Plan 11 (permissive default before it)
        if decision is not allowed: mark(node, decision); continue
        if cached := store.find_run(fingerprint=node.fingerprint, status=done):
            reuse(node, cached); continue       # "done + unaffected -> output cached"
        if pools.try_acquire(node.resources): spawn(node_runner.run(node))
        else: mark(node, "queued")
    event = await next(completion | graph_changed | pool_released | pause_changed)
    persist(event)
    if event.is_important(): session.inbox.put(Notification(event))
```

- **Pools:** `execution/resources.py` gives each pool (`llm`, `cpu_heavy`, `gpu`, `monai_inference`, `orthanc_io`) an asyncio semaphore sized from `config/resources.yaml`, plus `max_parallel_nodes` per session. Celery queues remain the real limit for jobs.
- **Important events** (system §22): a node fails after automatic retries, an expectation is violated, or the current graph finishes (`graph_idle`). A normal success is only persisted and shown; it does not wake Main.
- **Lifecycle:** the Executor exits when nothing is left to run, and starts again on the next `graph_changed`. The session goes to `idle`, never "finished" (§28).

## Phase 10.3 — Expectations (`execution/expectations.py`)

- After a node succeeds, check its `expect` list against the summaries of its output artifacts.
- **On a violation** (e.g. `cohort_size < 30`):
  - the node stays `done`;
  - its dependents become `held` (`hold_reason="expectation_violated"`), and the session becomes `blocked`;
  - an `expectation_violated` event is emitted and sent to the inbox.
- Main resolves it with a diff (`ReleaseHold`, `UpdateParams`, ...).

## Phase 10.4 — Live changes

On `graph_changed`:
- cancel `runs_to_cancel`. This means the cancel token for agent nodes and `JobClient.cancel` for jobs; MONAI cancel arrives in Plan 15.
- invalidated nodes run again as new attempts; new nodes are picked up.

Work added after the graph went idle simply starts a new Executor.

## Phase 10.5 — Watchdog (`execution/watchdog.py`)

1. **What it watches:**
   - **agent nodes:** a heartbeat (`NODE_HEARTBEAT_TIMEOUT_S`) and `timeout_s`;
   - **job nodes:** the job record (`heartbeat_at`, `TIMED_OUT`, `INTERRUPTED`, `error_code="oom"`).
2. **How it classifies failures:** `timeout`, `oom`, `interrupted`, `error` and `resource_unavailable`.
3. **Automatic retries** (deterministic, not LLM): only for `interrupted` and `resource_unavailable`, only when the spec is `idempotent`, and only while `attempt < max_attempts`. It uses `RetryPolicy`. All other failures go to the inbox.

## Phase 10.6 — Control and restart

1. **Pause:** stop launching nodes. Agent nodes pause at the `gate`. Jobs cannot pause; they finish or are cancelled.
2. **Resume:** continue. **Stop:** cancel every running node run; the nodes become `cancelled` and the session `idle`.
3. **Restart recovery** (in the app lifespan; Plan 17 wires it):
   - load the sessions that have active nodes;
   - agent node runs that were `running` → `failed(interrupted)`, because in-process work is lost;
   - job nodes → re-attach with the deterministic `job_id` (`submit` returns the existing record);
   - send an `execution_resumed` notification.

**Tests (`tests/unit/execution/`,** using `FakeAgentEngine`, the in-memory `JobClient` and `InMemoryGraphStore`):
- A→B→C readiness in order (§12);
- the Executor keeps running while the inbox is busy (§25);
- a cache hit (no new run); cancel of an affected running node; invalidation re-runs;
- the expectation hold; the watchdog mapping of timeout / OOM / interrupted; retry only when idempotent;
- the pause gate; restart re-attach; pool limits → `queued`;
- the input → param mapping; artifacts recorded with the `phi` label.

**Done when:** a 3-node graph runs to completion on fakes, and every rule above has a passing test.

---

# Plan 11 — Runtime Guardrails, Modes, Approvals

**Goal:** one place that decides execution boundaries (system §10). It also covers Plan/Auto modes and approvals (§31).

## Phase 11.1 — Guardrails core (`harness/guardrails.py`)

1. **The contract:**
   - `check(action, ctx) -> Decision(outcome, code, detail)`, where `outcome` ∈ `allowed | approval_required | permission_denied | invalid | resource_unavailable`;
   - action kinds: `ToolCall` (by Main or an agent), `GraphChange`, `NodeStart`, `ModelCall`, `ExternalCall`.
2. **Hook points:**
   - `dispatch.invoke` `before_invoke` (Plan 1);
   - the Diff Engine `apply` / `propose`;
   - the Executor `NodeStart`;
   - the Model Gateway (`ModelCall`);
   - the MCP edge (`ExternalCall`, Plan 14).
3. **Rules** (small functions, evaluated in order; the first non-`allowed` result wins):

   | Rule | Outcome |
   |---|---|
   | Schema / Validator errors | `invalid` (with the codes) |
   | The caller's visibility does not include the tool | `permission_denied` |
   | Main calls a tool directly that is not `inline`, or that is `heavy` | `invalid: REQUIRES_NODE` |
   | Sandbox `runtime_check` (Plan 4) | `permission_denied` / `resource_unavailable` |
   | A pool or queue is not alive (`cubeai:queue:*:alive`, MONAI health) | `resource_unavailable` |
   | Budgets: session tokens (`SESSION_TOKEN_BUDGET`), active nodes, `max_attempts` | `permission_denied: BUDGET_EXCEEDED` |
   | Mandatory approvals: `destructive=True`, `trust != first_party` (unless the tool is in the MCP `auto_approve` list), `generated_code` | `approval_required` |
   | Plan mode (Phase 11.2) | `approval_required: PLAN_MODE` |
   | PHI / privacy (Plan 13), external access (Plan 14) | `permission_denied` |

4. **Test:** a unit test asserts that no rule reads the request text, counts steps, or looks at `cost` to pick a strategy.

## Phase 11.2 — Modes (Plan / Auto)

- `execution_mode` is set per session (`PATCH /session/{id}/settings` on the Agent System, which calls the Application API). **The default is `auto`** (decided 2026-10-01). Plan mode is opt-in, for users who want to approve every piece of work. The mandatory approvals below still apply in Auto mode, so the risky actions keep a human gate either way.
- **Plan mode:**
  - diffs that add, modify, restart or release work → `propose` + an approval row, and the session becomes `awaiting_approval`;
  - diffs that only remove pending nodes or cancel work are `allowed`;
  - Main's direct calls to `side_effects="writes"` tools → `approval_required`;
  - read-only calls are always allowed.
- **Auto mode:** valid diffs are applied immediately.
- **Both modes:** the mandatory approvals always apply.

## Phase 11.3 — Approvals (`harness/approvals.py`)

1. **When approval is required:**
   - create the approval (the Application API) and emit `approval_requested`;
   - return to the caller `RunAccepted{status: "awaiting_approval", approval_id}`, or a tool result with the same fields.
2. **Deciding** is done only by the user, never by an LLM:
   - REST: `POST /session/{id}/approvals/{approval_id}` with `{decision: approve|reject, comment}`;
   - deterministic chat commands, handled before the LLM in the input route: `/approve [id]`, `/reject [id] [reason]`, and the legacy text `APPROVE` when exactly one approval is pending (the current canvas buttons send it).
3. **On approve:**
   - validate again, then apply with CAS;
   - if the base moved but none of the touched nodes changed, rebase automatically; otherwise mark the approval `superseded` and notify Main (`STALE_BASE`).
   - For node approvals, release the node.
4. **Every decision** → an `approval_decided` event and an inbox notification. **Expiry:** `APPROVAL_TTL_H`.

**Tests (`tests/unit/harness/test_guardrails.py`, `test_approvals.py`):**
- each rule outcome; order and short-circuit;
- a new session is in Auto mode;
- Plan vs Auto on the same diff; removals allowed in Plan mode;
- mandatory approvals in Auto mode;
- the chat commands never reach the LLM; `APPROVE` with 0, 1 or 2 pending approvals;
- the rebase vs superseded paths;
- the "no strategy rule" scan.

**Done when:** every action kind passes through `check()`, and the tests pass.

---

# Plan 12 — Session Layer: Inbox, Main Loop, Context, Skills, Planner

**Goal:** the Main Agent as a free, non-blocking orchestrator (system §5-§8, §23, §25, §30, §32, §33).

## Phase 12.1 — Session Inbox (`harness/inbox.py`)

1. **Item types:**
   - `UserMessage`;
   - `Notification(kind: node_failed | expectation_violated | graph_idle | approval_decided | execution_resumed | resource_unavailable)`;
   - `Command`.
2. **It replaces `ConnectionManager.message_queues`** (`socket_store.py:86-98,187,224`) and keeps the Idempotency-Key dedupe.
3. **`POST /session/{id}/input`** turns `/skills`, `/approve` and `/reject` into Commands, handled without the LLM. Anything else becomes a `UserMessage`.
4. **Coalescing:** notifications that arrive during a Main turn are merged into one message for the next turn.

## Phase 12.2 — Control tools (`toolpacks/control/`, `visibility={"main"}`, inline)

All control tools are `light`, except `plan` and `recover`, which are `medium` with a hard timeout.

| Tool | Does | Returns |
|---|---|---|
| `registry_search(query, kind?)` | Lexical search over `catalog.index("main")` | Compact entries |
| `registry_describe(name)` | `catalog.describe` | Schema, ports, manifest |
| `graph_inspect(node_id?)` | The graph summary, or one node's details (error, log tail, artifacts) | Summary |
| `execute(target, params, bindings?, after?, expect?)` | A one-node diff through the Guardrails and the Diff Engine | `RunAccepted` |
| `graph_apply(diff \| proposal_id, base_version, on_running?)` | Applies or proposes a diff | `{version, status: applied \| awaiting_approval, approval_id?, affected, errors}` |
| `node_cancel(node_id)` | Cancels a running or queued node | Status |
| `node_message(node_id, text)` | Adds a user interruption to a running agent node's mailbox | Ack |
| `artifact_read(artifact_id)` | The artifact summary and ref (never the raw data) | Summary |
| `skill_load(name)` | The skill body | Text |
| `plan(request, focus_node_ids?)` | Phase 12.5 | `GraphProposal` |

`execute` and `graph_apply` **never wait for execution**. They return as soon as the version is applied or proposed (§14).

## Phase 12.3 — Context Manager (`harness/context_manager.py`)

1. **Main's context** (§32):
   - the system prompt from `main_agent/agent.md`;
   - the recent chat (from the Application API chat messages);
   - the session summary (`sessions.context_summary`) and a compact graph summary;
   - the skill index; the compact registry index (`catalog.index("main")`);
   - the schemas loaded so far (`registry_describe` results).
2. **Compaction:** above `MAIN_CONTEXT_TOKEN_BUDGET`, the older turns are summarized with the `summarizer` profile and saved with `PUT /sessions/{id}/context-summary`.
3. **The Composer's context:** the request, the current graph, the relevant registry details (search hits + described entries), the skills with `applies_to: composer`, and the artifact types.
4. **An execution agent's context:** its own prompt, the node inputs, the node instructions (`params.task`), its own tool schemas and mailbox messages.

## Phase 12.4 — Main Loop (`harness/main_loop.py`, `harness/session_runtime.py`)

1. **`SessionRuntime`:** one per active session. It owns the inbox, the Main loop task and the `SessionExecutor`. It starts lazily on the first inbox item and stops after `SESSION_IDLE_TIMEOUT_S`.
2. **The loop** (system §23):
   ```
   async def session_loop(rt):
       while True:
           item = await rt.inbox.get()
           if item.is_command: await handle_command(rt, item); continue
           ctx_msgs = context_manager.for_main(rt, item)
           result = await engine.run(main_agent, task=item.as_message(), inputs=ctx_msgs, ctx=rt.main_ctx, cancel=rt.turn_cancel)
           await reply(rt, result.output)     # `msg` event + persisted chat message
   ```
   The inner LLM ↔ tool loop is `LangGraphAgentEngine` (Plan 9).
3. **Limits:** each turn has `MAIN_MAX_TOOL_ITERATIONS` and a wall-clock budget. On an error, Main sends a short failure reply, and the error is logged.
4. **Main never awaits a node**, for two reasons: the control tools return at once, and a direct call to heavy work is refused (`REQUIRES_NODE`).

## Phase 12.5 — Planner integration

1. **`plan`** = `make_agent_tool("plan", "composer", GraphProposal, post=validate_and_repair)`.
2. **`validate_and_repair`** runs the Validator. When there are errors, it sends them back to the Composer for up to `PLAN_REPAIR_ROUNDS` rounds. It then returns the proposal together with any errors that remain.
3. **The proposal is never applied by `plan`.** Main decides, and then calls `graph_apply(proposal_id=...)`.

## Phase 12.6 — Skills Loader (`harness/skills_loader.py`)

- Scans `skills/*.md` and builds an index per audience (`applies_to`).
- `/skills` lists the index (a Command). `skill_load` returns a skill's body.

## Phase 12.7 — Agent System routes and events

1. **New router `api/routes/graph.py`,** added to the `include_router` loop in `app.py` (keep `files.router` first):
   - `GET /session/{id}/graph`;
   - `POST /session/{id}/graph/diff` (user edits; it goes through the Guardrails);
   - `POST /session/{id}/approvals/{approval_id}`;
   - `PATCH /session/{id}/settings`;
   - `POST /session/{id}/nodes/{node_id}/cancel`.
2. **Register the new events in `services/event_registry.py`:**
   - **durable:** `graph_version_proposed`, `graph_version_applied`, `graph_version_rejected`, `node_status`, `artifact_created`, `approval_requested`, `approval_decided`, `expectation_violated`, `session_state`;
   - **ephemeral:** progress keeps using `tool_progress`, and streaming keeps using `stream_chunk`.
3. **Update `_SOURCES`** in `tests/unit/services/test_event_registry.py` to scan `harness/`, `execution/`, `planning/` and `toolpacks/`.

**Tests (`tests/unit/harness/`):**
- inbox order and dedupe; commands skip the LLM; coalescing;
- **a Main turn with a fake model:** `execute` returns `RunAccepted` while a fake node runs, and a second user question is answered before the node finishes (§6);
- `plan` never applies anything; the repair rounds;
- context contents and compaction; the skills index;
- the routes; the event registry scan.

**Done when:** on fakes, a user message → Main → `execute` → Executor → `node_status` done → `graph_idle` notification → Main reply works in tests.

---

# Plan 13 — Recovery and Privacy

**Goal:** system Phase 3: the Recovery Agent, resource failure handling and PHI-Local.

## Phase 13.1 — Recovery Agent (`execution/recovery.py`, control tool `recover`)

1. **`recover(node_id)`** = `make_agent_tool("recover", "recovery_agent", RecoveryProposal, post=validate)`. It is visible only to Main.
2. **Its context:** the node spec and params, the `error_code`, the log tail (the job dir or the agent run notes) and the graph neighbourhood.
3. **What it returns:** a diagnosis, an optional validated `GraphDiff` (e.g. new params, a smaller cohort, another model) and a message for the user. It **never applies anything**; Main decides.
4. **Order:** the deterministic retries of Phase 10.5 always run first. Only failures that remain reach Main.

## Phase 13.2 — Resource failure handling

1. **Mapping to `resource_unavailable`:**
   - a queue with no live worker;
   - MONAI 429 after the `unavailable_s` budget (Plan 15);
   - a GPU queue that is declared but not started;
   - Orthanc or Application API health failures.
2. **What happens:** the node becomes `held` (`hold_reason="resource_unavailable"`), and a notification goes to the inbox.
3. **Automatic release:** the Watchdog probes held pools every `RESOURCE_PROBE_S`. It releases the holds automatically when the resource returns. This is a technical rule, not a strategy.

## Phase 13.3 — PHI-Local (`harness/privacy.py`)

1. **The setting:** `privacy_mode` (`standard|phi_local`) is set per session.
2. **In `phi_local`:**
   - the Model Gateway `locality_filter` keeps only `locality: local` models. If there is none, the Guardrails return `permission_denied: NO_LOCAL_MODEL`.
   - `config/models.yaml` marks `gpt-oss:120b-cloud` as `external`, and ships a commented `local_default` Ollama entry.
3. **PHI labels:** artifacts with `phi=true` and tools with PHI scopes mark their content. The Context Manager never puts PHI-marked content into a context that goes to an external model. It sends refs and non-PHI summaries instead.
4. **In `standard` mode:** access is normal, as configured.
5. **Everywhere:** the MCP edge refuses PHI args (Plan 14), and the audit logs hash patient identifiers.

**Tests:**
- recovery returns a valid diff or none, and never applies it;
- held → released when the probe succeeds;
- `phi_local` with and without a local model; PHI content is filtered out of external contexts;
- `standard` mode is unchanged; audit redaction.

**Done when:** the three areas are covered by passing tests.

---

# Plan 14 — MCP Outbound Edge (zero servers)

**Goal:** third-party MCP servers can be plugged in later as untrusted tools. Today there are none.

## Phase 14.1 — Config and connections

1. **`toolpacks/external_mcp/config.py`:** a pydantic `McpServerConfig` with name, transport (`stdio` \| `streamable_http`), command/url, tool allowlist, env allowlist, secret refs, timeouts, `allow_phi=false` and `auto_approve=[]`. It is loaded from `config/mcp_servers.yaml` → `servers: []`.
2. **`manager.py`:**
   - one **persistent** `ClientSession` per server, never one per call;
   - a start-up timeout; a cached tool list; reconnect with backoff;
   - `start()` / `stop()` for the app lifespan (wired in Plan 17).

## Phase 14.2 — Import, policy, guardrails

1. **`importer.py`:** turns each MCP tool into a `ToolSpec`:
   - name `mcp__<server>__<tool>`; `input_schema` from the MCP schema;
   - `trust="third_party"`, `execution="external"`, `visibility={"agent"}`;
   - `side_effects="writes"` unless the MCP annotations say read-only;
   - stdio servers get `isolation="container"`; HTTP servers get an egress allowlist.
2. **`policy.py`** (a Guardrails rule provider for `ExternalCall`):
   - deny by default; a tool allowlist per server;
   - no identity or context is sent;
   - `phi_read` args are refused unless `allow_phi` is set **and** the session is not `phi_local`;
   - limits on argument and result size;
   - `approval_required` unless the tool is listed in `auto_approve`;
   - results are marked `{"untrusted": true, "source": "mcp:<server>"}`.
3. **`audit.py`:** one log line per call (server, tool, arg digest, duration, status).
4. **`toolkit/runtime/external.py`:** routes `execution="external"` through the Guardrails and the manager.
5. **stdio servers are refused** while `SANDBOX_CONTAINER_ENABLED=0` (Plan 4).

**Tests (`tests/unit/toolpacks/external_mcp/`):** a fake server over the SDK's in-memory transport (`mcp.shared.memory`) checks:
- zero servers → zero tools; prefixed names;
- the deny-by-default allowlist; `phi_read` refused; `approval_required` by default; results marked untrusted;
- one session reused across calls;
- a stdio server refused while containers are disabled.

**Done when:** the catalog loads the edge with zero tools, and its tests pass.

---

# Plan 15 — MONAI Inference Jobs + Segmentation Client

**Goal:** fix the critical MONAI job bugs, and move the segmentation client onto the shared contract, so that the Executor can cancel it and classify its failures.

## Phase 15.1 — Safety net

Add the first real tests for `monailabel/endpoints/inference_jobs.py`: submit, poll, resubmit after each terminal state, cancel while queued, and the restart scan. The resubmit test reproduces J1.

## Phase 15.2 — MONAI server fixes

Fix these in place. MONAI keeps its own file store, and its status strings must equal `jobs_core.status` (a contract test checks this).
- J1, J2, J4 (server side), J6, J8, J10, J11, J12.
- Verify J3 in the 0.8.5 image.
- J9 ingress rules in `Frontend/OHIF-Viewer/platform/app/.recipes/OpenResty-Orthanc/config/nginx.conf`.

## Phase 15.3 — MONAI backend

`jobs_core/backends/monai_http.py` (`MonaiHttpBackend` implements `JobClient`):
- honours `Retry-After`; uses a closed status set; uses separate budgets;
- streams the result download, with retry;
- cancels and purges in `finally`;
- 429 after the `unavailable_s` budget → `error_code="resource_unavailable"` (Plan 13).

## Phase 15.4 — Segmentation client

- `segmentation/services.py` `_await_inference_job` → `MonaiHttpBackend` (J7, J13, S1, S3, S4).
- The job id includes a params digest (S5, `module.py:1439-1441`).
- The heartbeat in `pipelines/common.py`: J14, S2.
- Use `to_thread` for heavy numpy / SimpleITK work (S7).
- The session handle is passed in explicitly (S9); the Executor's cancel token reaches the MONAI cancel.
- `_wait_for_review` uses `RetryPolicy` (S10).
- The `toolpacks/segmentation/run_segmentation.py` tool also uses `MonaiHttpBackend`.

**Tests (`tests/unit/monai/`, `tests/unit/segmentation/`):**
- J1 resubmit + slot count; J2 slot kept until the work ends;
- `INTERRUPTED` handling; the TTL sweep; the status-string contract;
- backend tests with `respx`, including the 429 → `resource_unavailable` mapping;
- the heartbeat never leaks the inner task;
- a finished result is kept after a long pause;
- a node cancel reaches MONAI.

**Done when:** the J1 test is green, and both clients use `MonaiHttpBackend`.

---

# Plan 16 — Other Job Systems

**Goal:** fix the remaining long waits, training, ingestion and background tasks.

## Phase 16.1 — Long waits

- **S11:** the review long-poll server is keyed by batch, leaks no waiters, and checks for disconnects.
- **S13 (server side):** compile before taking the row lock, with `asyncio.create_subprocess_exec` in its own process group, killed on timeout.
- **S13 (client side):** `latex_to_pdf` retries and resolves the 409 `stale_version`.
- **S8:** PATCH only the changed pair.

## Phase 16.2 — Training

Fix T1–T9. T1 becomes one server-side training job (prepare → create-model → train), orchestrated by the Application API, with the job id on the workflow row (an Alembic migration after "o"). Training stays outside the Session Graph; it belongs to the MONAI training UI.

## Phase 16.3 — Ingestion on Celery

Ingestion conversion becomes a Celery job (`job_name="ingest_convert"`, registered as a `harness`-visible job tool). This fixes G2, G3 and G6 by design. Then fix G1, G4, G5, G7 (one frontend upload client) and G8.

## Phase 16.4 — Agent System tasks

- **K1:** use `best_effort.schedule_best_effort`.
- **K2:** stop ordering, applied to the new `SessionRuntime` stop: set the status only after the tasks end, and re-raise `CancelledError`.
- **K3:** `reload` only when `APP_ENV=dev`.

**Tests:**
- S11 waiter cleanup; S13 does not block the event loop (a fake slow compiler);
- the training job state machine;
- G1: a failed re-run does not delete an existing series;
- ingestion job cancel / `INTERRUPTED`;
- K2 stop ordering.

**Done when:** every listed problem has a test that passes.

---

# Plan 17 — Cut-over, AutoGen Removal, Cleanup, Docs

**Goal:** the harness becomes the only execution path, AutoGen is deleted, and the docs describe the new system.

## Phase 17.1 — API cut-over (Agent System)

1. **App lifespan (`app.py`):**
   - build the catalog and run `validate()` (a failure stops start-up);
   - start `SessionRuntimeManager` (restart recovery, Phase 10.6) and the MCP manager (Plan 14);
   - keep `ensure_storage_layout()` and `install_write_guard()` after every import.
2. **`POST /session/{id}/input`** → the Session Inbox (Plan 12).
3. **`/pause`, `/resume`, `/stop`:** the same paths and bodies, now driving `SessionExecutor` control (Phase 10.6).
4. **Rebuilt from graph state:** `GET /status/{id}`, `/session/{id}/execution-state` and `/session/{id}/recover`.
5. **Retired, returning `410 Gone` with a short message:** `/plan`, `/run`, `/session/{id}/resume-pipeline`. The frontend update belongs to the Surface plan.
6. **Application API:** new sessions start `idle`. `approve_plan` / `create_pending_runs` and `CANONICAL_AGENT_TYPES` stay only for legacy data. The empty `plans` row created with every session stays, because a deferred trigger needs it. **No table is dropped.**

## Phase 17.2 — Delete AutoGen

1. **Move the segmentation domain module first:** `agents/execution_agents/segmentation/` → `agent_system/segmentation/`, imported only as `agent_system.segmentation.*`. Before the move, check that nothing does a bare `import segmentation`. Update the Plan 15 paths and the tests.
2. **Delete:**
   - `agents/execution_agents/*_agent.py` and `agents/planning_agents/`;
   - `agents/literature_review_agent/` (its tools and prompts were copied in Plans 2 and 3; keep `knowledge_builder.py` only if a tool still imports it, moved into `toolpacks/literature/`);
   - `agents/run_bound_agent.py`, `teams/`, `api/runners.py`, `services/pipeline_monitor.py`, `task_manager/`, `system_prompts/`;
   - the unused `Session` fields (`planning_task`, `execution_task`, `input_queue`, `output_queue`, `plan`, `resume_from_step_id`).
3. **Dependencies:** remove the `autogen-*` packages from the requirements and the image.
4. **Tests:** delete the tests marked `legacy_autogen`, then the marker and the conftest auto-mark rules. The deterministic suite goes back to `unit or contract or pipeline`.

## Phase 17.3 — Remove the internal emit path

Nobody posts to it any more, so remove:
- the `/events` and `/ws-broadcast` routes (`api/routes/events.py`);
- `services/internal_auth.py` and `segmentation/ws_client_sender.py` (its only user, `tests/manual/upload_pdf_smoke.py`, is updated or deleted);
- the `WS_INTERNAL_TOKEN` start-up check in `app.py`, and its notes in `deploy/.env.example:29,80`;
- `tests/unit/api/test_events_routes.py`.

## Phase 17.4 — Search checks

- No `autogen` import anywhere in `Backend/`.
- No `FastMCP`, `@mcp.tool`, `StdioServerParams`, `mcp_server_tools`, `Backend/mcp` or `from tools import mcp`.
- No identity `os.environ` reads in `toolpacks/`.
- No top-level `types`, `tools` or regular `config` package in `agent_system/`.
- `modeling_agent` appears only in the legacy producer allowlist and in the migrations.
- No `pycaret` import or requirement anywhere in `Backend/` or `deploy/`, and no `pycaret_*` tool name outside old migrations and docs history.
- No backend caller of `/plan` or `/run`, and no `Backend/mcp` in `deploy/`, `scripts/` or the launchers.

## Phase 17.5 — Docs

1. **`CLAUDE.md`:**
   - the service table (Redis, compute worker, compute beat);
   - the "Agent pipeline" section, rewritten as the harness (Main Agent, Session Graph, Executor, Guardrails, modes);
   - the Toolkit (`ToolSpec` / `AgentSpec` / `run_agent`, packs, catalog, `agent.md`, skills);
   - the MCP edge; the sandbox; the new env vars;
   - the modeling engine (scikit-learn, no PyCaret) and the renamed modeling tools (Python 3.13 itself is documented in Plan 0);
   - replace the "no GPU lease / no `WAITING_FOR_RESOURCE`" note with the resource pools and the `queued` status;
   - the single-worker rule still holds.
2. **`cubeai-docs/`:**
   - `agent-system/harness.md` (the architecture above);
   - `jobs/README.md` (the runbook) and `security/sandbox.md`;
   - io-contracts per execution agent, plus a `run_modeling` tool contract;
   - `backend/testing.md`;
   - mark "AG-3 Tool / MCP adapter" in `refactoring/execution-plan.md` as replaced.

## Phase 17.6 — Final test pass

Run every suite in the Testing section, plus the final check.

---

## Definition of Ready — every tool

1. Registered with `@tool` and a full manifest: pack, execution, side_effects, cost, resources, trust, isolation, data_scopes, visibility, timeout. The manifest rules hold.
2. Graph-visible tools declare their ports, with known artifact types.
3. Identity comes only from `current_context()`: no identity env reads, no module constants.
4. No module-level mutable state. Settings are read lazily and validated.
5. Expected failures return `error_result`. No traceback reaches the LLM; it is logged instead.
6. HTTP goes through the shared client with explicit timeouts.
7. `inline` tools do no sync work longer than about 50 ms on the event loop (use `asyncio.to_thread`).
8. Idempotent, or marked `idempotent=False`. `job` tools take JSON args only.
9. Temp files go only in per-call / per-job dirs from `storage_paths`.
10. There is a unit test with a fake context: one success and one error.
11. The name is unchanged. The only exceptions are `save_literature_output` (Plan 2) and the 5 renamed modeling tools (Plan 7).
12. It runs on Python 3.13 and imports no `pycaret`.

## Definition of Ready — every agent

1. It has an `agent.md` that the loader validates, and a `kind`.
2. Every name in its allowed tools exists and is visible to agents. Every delegate is `internal`.
3. Execution agents declare their ports and read upstream data from "Node inputs".
4. `completion_check` / `output_contract` are set where the output feeds other nodes.
5. There is a test run on `LangGraphAgentEngine` with a scripted fake model.

## Testing (the work only — no end-to-end run)

| Suite | Command |
|---|---|
| Runtime (Plan 0) | `uv pip compile Backend/agent_system/requirements.txt --python-version 3.13 --python-platform x86_64-manylinux_2_28`, then `python -m pytest -c Backend/pytest.ini Backend/tests/runtime -m docker` against the built agents image |
| Modeling engine | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/toolpacks/modeling` |
| Deterministic gate | `powershell -NoProfile -ExecutionPolicy Bypass -File Backend/tests/run_tests.ps1 -Suite deterministic` |
| Toolkit / agents / engines | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/toolkit Backend/tests/unit/agentpacks Backend/tests/unit/engines` |
| Tool packs | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/toolpacks Backend/tests/unit/functions` |
| Harness / planning / execution | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/harness Backend/tests/unit/planning Backend/tests/unit/execution` |
| Sandbox | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/sandbox` (add `-m docker` for real containers) |
| Jobs (unit) | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/jobs` |
| Jobs (integration) | `docker compose --env-file deploy/.env -f deploy/docker-compose.backend.gpu.yml -f deploy/docker-compose.backend.dev.yml up -d redis compute-worker-cpu compute-beat`, then `python -m pytest -c Backend/pytest.ini Backend/tests/integration/jobs -m "integration and celery"` |
| State store (integration) | Start `postgres` + `db-migrate` with the same compose files, then `python -m pytest -c Backend/pytest.ini Backend/tests/integration -m "integration and docker" -k graph` |
| Schema contracts | `python -m pytest -c Backend/pytest.ini Backend/tests/contracts` |
| MONAI / segmentation | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/monai Backend/tests/unit/segmentation` |
| Storage boundary | `powershell -ExecutionPolicy Bypass -File Backend/scripts/check_storage_boundary.ps1` |

**Final check:** `python -c "from agent_system.toolkit.catalog import build; c = build(); c.validate(); print(len(c.tools), len(c.agents))"` prints:
- **75 tools**: 55 moved + `run_segmentation_with_guard` + 6 literature + `run_agent` + `run_modeling` + 11 control tools (`registry_search`, `registry_describe`, `graph_inspect`, `execute`, `graph_apply`, `node_cancel`, `node_message`, `artifact_read`, `skill_load`, `plan`, `recover`);
- **16 agents.**

The MCP edge adds zero.

## Traceability

**system_plan → merged plans**

| system_plan | Where |
|---|---|
| §1–2 Core idea, control vs execution plane | Principles; Plans 10–12 |
| §3 Surface | Not in this roadmap (the backend endpoints are ready: Plans 11.3, 12.7) |
| §4 Main Agent, Context Manager, Skills Loader, Inbox | 3.4, 12.3, 12.6, 12.1 |
| §4 Runtime Guardrails, Model Gateway, State, Observability | 11, 9.2, 6, 9.2 usage + 10.1 timings (tracing later) |
| §5–6 Main decides, non-blocking loop | 12.2, 12.4 |
| §7–9 Planner, Composer, Validator | 3.4, 12.5, 8.2 |
| §10 Runtime Guardrails | 11.1 |
| §11–14 Execution, Executor, long-running agents, tracked execution | 10, 12.2 (`execute` → `RunAccepted`) |
| §15–16 Tool manifest, cost / side_effects | 1.1, 2.4, 7.1, 7.3 |
| §17–19 Node, graph ≠ execution, live Session Graph | 6.1, 8, 10 |
| §22 What wakes Main | 10.2, 10.3, 12.1 |
| §23–25 Main loop, Executor loop, independence | 12.4, 10.2 |
| §26–27 Diff Engine, affected set, diff rules | 8.3, 8.4, 10.4 |
| §28 No permanent finished workflow | 6.1, 10.2 |
| §29 Agents | 3 (modeling is the `run_modeling` tool, Plan 7) |
| §30 Skills | 3.5, 12.6 |
| §31 Modes, Privacy | 11.2, 13.3 |
| §32 Context | 12.3 |
| §33 Registry | 1.3, 12.2 |
| §34 Folder structure | Target layout (with the renames of decision 1) |
| §35 Phases 1–3 | M3, M4 |
| §35 Phase 4 | Not in this roadmap |
| §36 Evaluation | Not in this roadmap |

**tools_plan → merged plans**

| tools_plan | Merged |
|---|---|
| – (new) | 0 (Python 3.13, PyCaret removed) and 7.0–7.2 (the scikit-learn modeling engine, renames, legacy records) |
| P1 | 1, plus 10.1 (RunHarness, DirectToolExecutor) and 6 (the CANONICAL contract replaced) |
| P2 | 2 |
| P3 | 3 (`modeling_agent` removed; control agents and skills added) |
| P4 | 4 |
| P5 | 5 |
| P6 | 7 (on the new engine, with the 5 modeling tools renamed) |
| P7 | 14 |
| P8 | 15 |
| P9 | 16 |
| P10 | 17 |

---

## Appendix — Problem catalog

### A. MONAI inference jobs (Plan 15; J5 is handled through the Plan 10 pools and Plan 16)

| ID | Problem | Fix |
|---|---|---|
| J1 | **Critical.** Resubmitting a job in a terminal non-success state calls `reserve()` (`inference_jobs.py:282`), then `_write_job(QUEUED)` (`:305`) hits the terminal guard (`job_root.py:133-143`) → 500, and the slot leaks. After 9 retries, all inference returns 429 until a restart. A normal Stop → Resume triggers it. | Explicit re-queue (`attempt + 1`); write the state before reserving; release on any error. |
| J2 | Cancel and timeout only close the loopback call; the GPU work continues, but the slot is freed. | Keep the slot until `/infer` returns; persist `CANCELLING` → `CANCELLED`. |
| J3 | (to verify) `/infer` may block MONAI's event loop. | Check the 0.8.5 image; if it blocks, run the infer task in a thread. |
| J4 | Clients ignore `INTERRUPTED` (`services.py:301`, `run_segmentation.py:111`). | Shared vocabulary; unknown = final; re-queue once. |
| J5 | Training preparation uses the inference slot (`training_jobs.py:118`). | Named capacity pools (Plans 10, 16). |
| J6 | No server-side limit on queue wait. | `queue_timeout_s` → `TIMED_OUT(queue)`. |
| J7 | 429 is treated as permanent; `Retry-After` is ignored (`services.py:203-212`). | `RetryPolicy`. |
| J8 | Job files are never cleaned; `.result.tmp` is left after a crash. | TTL sweeper; start-up cleanup; purge in `finally`. |
| J9 | `/inference/` exposes `/restart` and the job endpoints to the browser. | Deny them at the ingress; internal token. |
| J10 | Blocking file I/O on MONAI's loop. | `to_thread`; create directories once. |
| J11 | `CANCELLING` is never persisted. | Persist it. |
| J12 | No progress, heartbeat, list or queue position. | Add the fields and a list endpoint. |
| J13 | Two different MONAI clients; unscoped ids. | One `MonaiHttpBackend`; run-scoped ids. |
| J14 | The heartbeat wrapper leaks the inner task on non-cancel errors (`pipelines/common.py:158-226`). | `finally: cancel + gather`. |

### B. Segmentation client and long waits (Plans 15, 16; S12 in Plan 2)

| ID | Problem | Fix |
|---|---|---|
| S1 | The unavailable grace period only logs (`services.py:224-284`). | An enforced `unavailable_s` budget. |
| S2 | The budgets don't nest; the deadline counts pause time and is checked before `task.done()`. | Check `done()` first; pause-aware; outer budget = sum + margin. |
| S3 | The result download is not retried, and it is fully buffered. | Streaming with retry. |
| S4 | Purge happens only on success paths. | Purge in `finally` + TTL. |
| S5 | Resume after a params change → 409. | A params digest in the job id. |
| S6 | Many fixed-interval polls with different rules. | `poll_until`. |
| S7 | Heavy numpy / SimpleITK work on the loop (`services.py:462,571,763`, `module.py:446-455`). | `to_thread`. |
| S8 | O(N²) checkpoint PATCHes (`batch_store.py:228-243`). | PATCH only the changed pair. |
| S9 | The cancel checks swallow `ImportError` / `KeyError` (`common.py:133-134,152-153`). | An explicit session handle. |
| S10 | `_wait_for_review` retries too few errors; a 5xx is fatal (`module.py:986-1015`). | `RetryPolicy` + an overall deadline. |
| S11 | The review long-poll is keyed by session, leaks tasks and ignores disconnects (`segmentation_review.py:682-705`). | Key by batch; cancel the waiter; `is_disconnected()`. |
| S12 | Cohort gate: one transient error ends the wait; the timeout is sleep-only; a global flag (`targets_matcher.py`). | `poll_until` + per-session state. |
| S13 | The LaTeX compile blocks the Application API loop while holding a row lock (`repositories/reports.py:298-304,346-354,433`); the client doesn't retry. | Compile before the lock with `create_subprocess_exec`; the client retries and resolves the 409. |

### C. Heavy tools (Plan 7; H19 and H20 in Plan 5)

| ID | Problem | Fix |
|---|---|---|
| H1 | The model path depends only on comparison + variant (`results.py:85-95`), so concurrent runs overwrite each other. | The job id dedupes; temp file + atomic rename; a digest in the file name. |
| H2 | Training re-reads the path instead of using the captured bytes (`results.py:240,285`). | Train from the bytes. |
| H3 | Plain `str` paths (`classification.py:82`, `regression.py:71,155,158`). | `WorkspacePath`. |
| H4 | The inference fallback may pick a `__refit_` file (`classification.py:830-837`, `regression.py:646-654`). | An exact match, or an error that lists the candidates. |
| H5 | `os.chdir` (`classification.py:537,548`; `regression.py:414-434`). | Absolute paths. |
| H6 | Global logger changes and leaked handlers (`classification.py:28-53`, `regression.py:29-41`, `features.py:72-87`). | A per-job handler in `child.py`. |
| H7 | PyCaret `verbose=True`. | Obsolete once PyCaret is removed (Phase 7.0): the engine never prints and logs through the per-job handler. |
| H8 | The regressor lacks the checks, progress, fold clamp and replay `chart_data`. | Same as the classifier. |
| H9 | A shared params YAML (`features.py:513`). | Per job. |
| H10 | A new process pool per mask (`features.py:846-850`). | One pool per job; a real `n_workers`. |
| H11 | A CUDA probe just to set a flag (`features.py:697-712`). | Remove it. |
| H12 | The analysis reads only rows created by the run (`repositories/radiomics.py:261`). | Link reused rows to the current run. |
| H13 | `write_agent_output` deletes the run's temp dirs while jobs are running (`agent_output.py:25-35,213`). | Per-job dirs; skip cleanup while jobs are active. |
| H14 | A shared plots dir; the figure key is not content-addressed (`features.py:1538,1589,1599`). | A per-job dir; a digest key. |
| H15 | `mutual_info` has no `random_state`. | `split_seed`. |
| H16 | RFE keeps all features (`features.py:1319,1647`). | `n_features_to_select=top_features`. |
| H17 | `set_key=top_{n}` ignores the method → 409. | Add a config digest. |
| H18 | Code outside `try` raises (`features.py:308,325,344,365,376-377,1084-1103`). | Validate in the input model; the child always writes a result. |
| H19 | Progress arrives in bursts. | A sync progress file relayed by the supervisor. |
| H20 | Writes are refused unless the run is `running` (`repositories/radiomics.py:59-78`). | The supervisor checks before it starts. |

### D. Light tools (Plan 2)

| ID | Tool | Problem | Fix |
|---|---|---|---|
| L1 | 14 DB tools | `raise_for_status()` raises; default 5 s timeout. | `error_result`; the shared client. |
| L2 | `get_all_patients_info`, `get_all_studies_info` | They raise, and dump whole tables. | `error_result`; `limit` / `skip`. |
| L3 | `get_invalidate_segmentations` | No filter. | Call `/segmentations/unvalidated/`. |
| L4 | `check_having_mask` | A 500 looks like "no mask"; duplicate logic. | `[]` only on 404; one implementation. |
| L5 | `validate_population_anatomy` | Matches empty or short text; raises. | A minimum length; `error_result`. |
| L6 | `extract_cohort_labels` | Sync `json.load`; `print()`; one LLM error drops every label. | `to_thread`; the logger; keep partial results. |
| L7 | `generate_targets` | A global flag; raises. | Per-session state; `error_result`. |
| L8 | `run_segmentation` | `float()` of an env var at import; a fixed temp name; tracebacks sent to the LLM; the wrong error flag. | Lazy validated settings; `mkstemp`; log tracebacks; the correct flag. |
| L9 | `browse_workspace` | The workspace path is not resolved. | `resolve()`. |
| L10 | `get_modeling_results` | Returns plain `"Error: ..."`. | `error_result`. |
| L11 | `list_models` | Returns a dict in one case and a JSON string in another. | One type. |
| L12 | `write_agent_output` | Sync `rmtree`. | `to_thread` (+ H13). |
| L13 | `run_segmentation_with_guard` | Two names. | One name; `critical=True`. |
| L14 | scratchpad prompts | Wrong argument names; the wrong agent id. | Fix the prompts in `agent.md`; register/status are harness-only. |

### E. Training (Plan 16)

- **T1:** the browser orchestrates training. Fix: one server-side job, with its id stored on the workflow row.
- **T2:** MONAI drops fields it does not declare. Fix: declare them, with `extra="forbid"`.
- **T3:** each run creates a new `Task*` folder. Fix: use the DB `dataset_id`.
- **T4:** the workflow is frozen before MONAI accepts the job. Fix: freeze it after the accept.
- **T5:** MONAI errors become a 503. Fix: pass the 4xx through.
- **T6:** preparation shares the inference slot. Fix: see J5.
- **T7:** cancel is not granular, and nothing is rolled back. Fix: check between files and delete partial data.
- **T8:** the reservation leaks, and `_workflow_locks` grows forever. Fix: `try/finally` and a `WeakValueDictionary`.
- **T9:** browser polling is fragile. Fix: backoff, and tolerance of transient errors.

### F. Ingestion (Plan 16, moved to Celery)

- **G1:** a failed re-run deletes a good series. Fix: delete only what this attempt created.
- **G2:** a long queue looks abandoned. Fix: a Celery queue + a heartbeat.
- **G3:** a restart leaves jobs stuck. Fix: `INTERRUPTED` through the sweep.
- **G4:** prune is never called. Fix: a beat task.
- **G5:** the webhook token is missing. Fix: send the header.
- **G6:** a blocking 2 GiB write and hash. Fix: chunked, in a thread.
- **G7:** no cancel or list endpoints, and two upload clients. Fix: add the endpoints and keep one client.
- **G8:** the status names differ. Fix: the shared `status` + a `stage` field.

### G. Agent System tasks (Plan 16)

- **K1:** fire-and-forget tasks are not referenced. Fix: `best_effort.schedule_best_effort`.
- **K2:** `stop` sets CANCELLED too early, and `CancelledError` is swallowed. Fix: set the status after the task ends, and re-raise.
- **K3:** `reload=True` is on in production. Fix: dev only.

## Not in this roadmap

- **The frontend Surface:** chat `/skills` UI, a live Graph Canvas with diff preview, the Approvals Panel, and Settings for mode, privacy and models. The backend routes and events are ready for it.
- **The evaluation harness** (system §36).
- **system Phase 4 (Scaling):** embedding-based registry retrieval, domain sub-planners, a Tool Builder, the Result Verifier, tracing and metrics beyond run usage and timings.
- **`spawn_agent` / `wait_agent`**, and cross-session caching.
- **Deployment and storage:** enabling the container or microVM runners in production, any real MCP server, and object storage (MinIO/S3); jobs keep the shared `/storage` mount.
- **Scaling the Agent System:** a multi-worker Agent System (it needs a shared inbox and executor ownership, e.g. in Redis), and a persistent LangGraph checkpointer.
