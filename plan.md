# CUBE.AI merged roadmap: Agent Harness + Tool Platform

## Context

Three design documents describe the next version of CUBE.AI:

- **`system_plan.md` (Agent Harness Architecture)** describes _how the system behaves_. A general **Main Agent** decides what should happen. A live, versioned **Session Graph** records the work that was submitted. An **Executor** is the only component that runs work. **Runtime Guardrails** own permissions, PHI, approvals and budgets. It also defines Plan/Auto modes, a Recovery Agent and Skills.
- **`tools_plan.md` (Tool Platform)** describes _what the system is built from_. It has a framework-neutral **Toolkit** (`ToolSpec`, `AgentSpec`, registries, catalog), tool packs, **Celery + Redis jobs** for heavy tools, a **sandbox**, an **outbound MCP edge**, and fixes for every known job bug.
- **`cubeai-persistence-design.md` (Persistence Design, revision 3.2)** describes _how the state is stored_: every table, column, constraint and transaction the Application API owns for the harness. It was written after the first two were merged, and it replaces every database detail they contained. Its full text is merged into this roadmap as **Appendix P**, and **Plan 6** implements it. The standalone file is kept with the same text; a change to one is made to both.

The two documents overlap: both define registries, tool metadata, agents and execution. They also conflict in several places: package names, modeling, synchronous delegation versus asynchronous submission, and the fixed linear plan contract. This roadmap merges them into **one ordered set of 18 small plans (Plan 0 to Plan 17)**, each with phases. The toolkit and job work become the foundation. The harness, the graph and the Executor are built on top of that foundation.

**Decisions taken with you for this merge:**

- **`run_modeling` is a tool.** There is no `modeling_agent` (system_plan wins over tools_plan here).
- **LangGraph is the agent engine** for every LLM agent, including the Main Agent. The Executor, Diff Engine and Validator stay custom code.
- **Agent nodes are LangGraph-native** (decided 2026-10-02). Their state is checkpointed through the Application API (`HttpCheckpointSaver`, Appendix P D27), so an agent node resumes after a restart instead of reasoning again. In-node gates (cohort approval, segmentation review, tool approvals) and pause **suspend** the attempt with `interrupt()` and resume it with `Command(resume=…)`; no process waits. Side effects inside a node run as LangGraph tasks, so a resume never repeats a finished tool call. Fan-out inside a node uses `Send`; the literature review is a fixed workflow graph; long agent nodes compact their own context; a rerun can start from a checkpoint of an earlier run (`seed_from`). The Main, the Composer and the Recovery Agent have **no** checkpointer (D21). There is no LangGraph Postgres saver, no LangGraph Server and no `BaseStore` (Plan 9).
- The scope includes the **cut-over and AutoGen removal** (Plan 17). The frontend Surface and the evaluation harness are **not** in this roadmap.
- **Python 3.13 for every image we build** (Agent System, compute worker, Application API), done first in Plan 0. The MONAI Label image keeps its own Python 3.10.
- **PyCaret is removed.** Modeling runs on scikit-learn, with LightGBM, XGBoost and SHAP. The 5 PyCaret tools are rewritten on that engine and **renamed** in Plan 7 (the only rename besides `save_literature_output`).
- **Appendix P (the persistence design) is the only database specification.** Plan 6 implements it, and every other plan uses its names, its write paths (D24) and its transactions T0–T10.
- **`execution_mode` defaults to `auto`** (decided 2026-10-01). Plan mode is opt-in; the mandatory approval reasons apply in both modes.
- **Execution is sequential only** in this roadmap (D5). The rules that follow from it are listed as current-phase constraints (Appendix P §P.4), so DAG execution can be added later without a rewrite.
- **One vocabulary (D20).** The schema and the code are renamed once, in Phase 6.1, before Plan 2 moves the tools. Every plan after that uses the new names (`node_runs`, `run_id`, `graph_nodes`, `node_id`, `session_events`, `stored_files`, `segmentation_batches`, …).
- **No dual path (D26).** The AutoGen path is not kept working against the new schema; the rows it wrote stay as history.

**What exists today (checked in the code):** these are the names from before Phase 6.1. Phase 6.1 renamed them in the code, and Appendix P §P.2 maps each one to its name now.

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
2. **Orchestration is not execution.** The Main Agent and the Composer only _describe_ work. Only the Executor _runs_ it.
3. **Long-running work is asynchronous.** Submitting work returns `RunAccepted` immediately, and the Main Agent never waits for a node.
4. **The Session Graph is live.** It is versioned, can change during execution, and is never "finished".
5. **Guardrails decide boundaries, never reasoning.** Each check returns exactly one of `allowed | approval_required | permission_denied | invalid | resource_unavailable`.
6. **A tool is a callable action (`ToolSpec`).** An agent is a definition (`AgentSpec`) and never a tool. Delegation is a tool.
7. **Heavy compute runs as Celery jobs** behind one `JobClient` contract. Isolation follows trust.
8. **The Application API owns all durable state, and the database enforces the invariants.** Graph changes, runs, attempts, approvals and fencing are rows and constraints (Appendix P), not conventions inside the Agent System.

## Compatibility decisions (how the two plans fit together)

| #   | Topic               | system_plan says                                                                        | tools_plan says                                                                        | Merged decision                                                                                                                                                                                                                                                                                                                                                                                                               |
| --- | ------------------- | --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Package layout      | `tools/registry.py`, `types/artifacts.py`, `agents/<id>/agent.md`, `harness/context.py` | `toolkit/`, `toolpacks/`, `agentpacks/`; no `platform`, `core`, `tools` or `mcp` names | Keep the tools_plan names. See the rename list below this table.                                                                                                                                                                                                                                                                                                                                                              |
| 2   | The word "harness"  | The session layer (Main loop, guardrails, inbox)                                        | `RunHarness` = per-run bookkeeping                                                     | "Harness" now means only the system_plan session layer. `RunHarness`, `node_resolver` and `DirectToolExecutor` move into `execution/node_runner.py` and `catalog.resolve()`.                                                                                                                                                                                                                                                  |
| 3   | Tool metadata       | Manifest: typed inputs/outputs, `side_effects`, `cost`, `resources`, `phi`, `ui`        | `ToolSpec`: execution, trust, isolation, data_scopes, visibility, budgets, hooks       | One `ToolSpec` with both sets of fields. Derived fields and consistency rules are checked by `catalog.validate()` (Phase 1.1).                                                                                                                                                                                                                                                                                                |
| 4   | Who may call what   | Main calls tools directly, or submits agents and tools as nodes                         | `visibility = planner \| agent \| harness`                                             | `visibility` is a **set** of `main`, `graph`, `agent` and `harness`. `planner` is renamed `graph` (it may become a Tool Node). Main may call a tool directly only if it is `inline` and not `heavy`. Everything else runs as a node.                                                                                                                                                                                          |
| 5   | Delegation          | Main never waits; the Planner is an Agent-as-Tool                                       | `run_agent` waits for the child agent                                                  | Main has **no** `run_agent`. It uses `execute` / `graph_apply`, which create an Agent Node and return `RunAccepted`. `run_agent` stays only for execution agents that call their internal agents, inside a node that is already tracked. `plan` and `recover` are short agent-as-tool calls: they return proposals and never execute anything.                                                                                |
| 6   | Modeling            | `run_modeling` tool; no Modeling Agent                                                  | `modeling_agent` stays an agent                                                        | `run_modeling` is a job tool (manifest from system §15). `modeling_agent` is not created, and its prompt becomes the `modeling` skill. PyCaret is removed: the 5 modeling tools are rewritten on a scikit-learn engine, renamed, and stay graph-visible job tools (Plan 7).                                                                                                                                                   |
| 7   | Agent engine        | Plain loop (pseudocode)                                                                 | `AgentEngine` protocol with a fake; LangGraph later                                    | `LangGraphAgentEngine` implements `AgentEngine` for all LLM agents. The inbox loop, Executor, Diff Engine and Validator are plain code, because a compiled LangGraph `StateGraph` cannot represent a graph that changes while it runs. **Agent nodes are LangGraph-native** (2026-10-02): `HttpCheckpointSaver` stores their checkpoints through the Application API (D1 holds; Appendix P D27), with `thread_id = run_id`, so every attempt of a run resumes the same thread and a rerun starts fresh. Gates and pause use `interrupt()` / `Command(resume=…)` and suspend the attempt (T10). The Main, the Composer and the Recovery Agent keep no checkpoint: each Main turn starts from the context that the Context Manager rebuilds from `chat_messages` (D21). |
| 8 | Runs | Nodes have Runs | Job ids use `agent_run_id`; identity comes from `ToolContext` | _Persistence:_ `agent_runs` is renamed `node_runs` and holds every node run, agent or tool. A run is created lazily when its node starts, together with attempt 1 (D8); every try is a row of the new `run_attempts` table (D9). Domain FKs become `created_by_run_id`. The job id is `sha256(run_id\|tool\|args)`, and the submission key adds `.{attempt_number}`. |
| 9 | Plan contract | Open graph, no fixed order | `planner_steps() == CANONICAL_AGENT_TYPES` | This contract is dropped. The Validator and the registry decide what is valid. A contract test checks the producer allowlists in the Application API against the catalog. _Persistence:_ `plans` and `plan_steps` are renamed `session_graphs` and `graph_nodes` and become the live graph (D4, D6); every mutation is a `graph_changes` row (D7). |
| 10 | Session status | `idle \| running \| awaiting_approval \| blocked`; nothing is ever "finished" | Uses the current statuses | _Persistence:_ `sessions.status` is renamed `control_state` and holds only intent: `active \| paused \| archived` (D13). The activity (`idle`, `running`, `pausing`, `awaiting_approval`, `blocked`) is derived and never stored. Nothing is ever `completed`. |
| 11 | Resources | The Executor acquires `resources` (e.g. `cpu_heavy`) | `resource_class` maps to a Celery queue; J5 asks for named pools | `resources` is the manifest field. `resource_class` is derived from it: `cpu_heavy`→`compute.cpu`, `gpu`→`compute.gpu`, `highmem`→`compute.highmem`. Executor pools are set in `config/resources.yaml` and are runtime only: a node waiting for a pool, or refused at start, is not stored; it is published as `node_start_refused` (D19). |
| 12 | Cache vs job dedupe | A fingerprint drives cache reuse | `job_id = sha256(agent_run_id\|job_name\|args)` | _Persistence:_ only the **job id** now. It re-attaches an attempt to the same run after a restart. Fingerprints and cross-version caching are deferred (Appendix P §P.10): sequential suffix invalidation keeps the unaffected prefix, and `execution_policy.fresh` covers fresh reruns. |
| 13 | Human gates | Approvals Panel; Plan mode | Not covered | _Persistence:_ every durable approval is a `graph_changes` row decided by a user who quotes its digest (D7). There is no `approvals` table and no start-time hold; approval reasons are computed when a change is proposed. The gates inside nodes (cohort approval, segmentation review) stay on their domain rows and are written through the D24 `user gate` path. The node waits through `ctx.user_gate()`, which `interrupt()`s the agent graph and **suspends** the attempt (T10): the wait is durable, survives a restart and holds no process. A tool approval inside a node (Phase 11.3) is the same kind of gate. |
| 14  | PHI                 | `phi` flag; PHI-Local mode                                                              | `data_scopes`; MCP `allow_phi`                                                         | `phi` is derived from `data_scopes`. Artifacts carry a `phi` label. The Model Gateway filters models by locality, and the MCP edge refuses PHI. `gpt-oss:120b-cloud` counts as **external**.                                                                                                                                                                                                                                  |
| 15 | Events | Graph Canvas; important events | `source` = agent type; `EventSink` | `source` = the node's ref name (an agent type or a tool name), or `main_agent` for the Main Agent. _Persistence:_ the durable types of Appendix P (`graph_change_*`, `node_status`, `run_attempt_*` (including `run_attempt_suspended/resumed`), `gate_resolved`, `expectation_violated`, `node_start_refused`, `graph_idle`, `session_state`, `approval_requested`) are registered in `event_registry.py`, and `pipeline_agent_status` is renamed `node_status` in Phase 6.1. |
| 16  | Internal agents     | Not mentioned                                                                           | AgentSpecs only                                                                        | Internal agents run through `AgentEngine`. The segmentation module calls the engine instead of AutoGen `AssistantAgent`. The 6 literature `FunctionTool`s become registered tools. The literature review is a **fixed workflow graph** (Phase 9.6): its 5 internal agents are steps of one `StateGraph`, not `run_agent` delegates, and each step is checkpointed in its own namespace. |
| 17  | Runtime             | Not covered                                                                             | Python 3.11.3; PyCaret kept                                                            | Python 3.13 for our images (Plan 0). PyCaret removed; modeling on scikit-learn (Plan 7). MONAI stays on its own Python 3.10 image.                                                                                                                                                                                                                                                                                            |
| 18 | Persistence | "State" in the session layer | Not covered | Appendix P (decisions D1–D27) is the database design. Plan 6 implements it in two cut-overs (D26). |
| 19 | Execution shape | DAG validity, bindings, cycles | Not covered | Sequential only (D5): the order is `graph_nodes.position`; inputs come from the strict D23 rule plus an optional `input_sources`; there is no edge table. A DAG later follows Appendix P §P.5 *No `node_connections` table*. |
| 20 | Agent state and fan-out inside a node | Not covered | Not covered | _Your decision (2026-10-02):_ agent-node reasoning state is a LangGraph checkpoint stored by the Application API (D27); `@task` makes finished tool calls survive a resume; `Send` fans out per-item LLM work inside one node (cohort labels, literature search and ranking). This is parallelism **inside** a node, like the segmentation fan-out (D10), so the graph stays sequential (D5). |

**Renames for decision 1 (layout):**

| system_plan path       | Merged path                                 | Why                                                          |
| ---------------------- | ------------------------------------------- | ------------------------------------------------------------ |
| `types/artifacts.py`   | `toolkit/artifacts/`                        | A `types` package shadows the stdlib `types` module.         |
| `tools/registry.py`    | `toolkit/tools/registry.py`                 | Legacy bare `tools.*` imports still exist.                   |
| `agents/<id>/agent.md` | `agentpacks/<pack>/<agent_type>/agent.md`   | `agents/` is the legacy AutoGen package, deleted in Plan 17. |
| `harness/context.py`   | `harness/context_manager.py`                | `toolkit/context.py` already holds `ToolContext`.            |
| `config/`              | `config/`, data only, with no `__init__.py` | `config` is too generic a name for a regular package.        |

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
- **Only the Application API gets `DATABASE_URL`.** All graph, change, run, attempt, agent-checkpoint and result state goes through its HTTP API, in the transactions T0–T10 of Appendix P §P.8.
- **LangGraph persistence goes through the Application API only.** The one checkpointer is `execution/checkpointer.py` (`HttpCheckpointSaver`). No `langgraph-checkpoint-postgres`, `PostgresSaver`, LangGraph Server / `langgraph_sdk` or `BaseStore` in this roadmap. A checkpoint is reasoning state, never a result: results are typed rows and `node_runs.outputs` (D27).
- **Every write on the execution path declares its D24 path** (`attempt`, `internal job`, `user gate`, `harness` or `migration`). The database rejects a write that declares none.
- **Names after Phase 6.1** follow the vocabulary of Appendix P §P.2. Old names appear only in migrations, in old durable events, and in the problem catalog, which describes today's code. Phase 6.1 renames identifiers inside tool code, never tool names, tool argument names or success result shapes.
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
                                    |  graph change (full target sequence, base_version) -> RunAccepted {node_id, change_id, status}
                                    v
EXECUTION PLANE  (agent_system/execution)
  Diff Engine --T1/T2--> Session State Store (Application API, Appendix P: session_graphs, graph_changes,
                         graph_nodes, node_runs, run_attempts, typed domain tables, session_events)
  Executor -> Node Runner -+-> Agent Node: AgentSpec on LangGraphAgentEngine (run_agent -> internal agents,
                           |               or a fixed workflow graph; Send fan-out; tool calls as @task)
                           |     HttpCheckpointSaver --checkpoints (thread = run_id)--> Application API (agent_checkpoints, D27)
                           |     gate / pause -> interrupt() -> attempt suspended (T10) -> Command(resume=...) on decision
                           +-> Tool Node:  dispatch.invoke -> inline
                                                          -> job      (JobClient -> Celery/Redis -> compute worker -> sandbox runner)
                                                          -> external (MCP edge, Plan 14)
  Watchdog (heartbeats, timeouts, OOM, interrupted; suspended attempts hold no process and are not watched)
  every start / finish / cancel / reconcile / suspend / resume is a transaction of the Application API (T4 / T6 / T7 / T5 / T10)
  important events (failure, expectation violated, graph idle, approval decided) -> Session Inbox
  gate decisions (gate_resolved) -> Executor (resumes the suspended node; the Main is not woken)
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
      engines/langgraph_engine.py engines/workflow.py              (Plan 9)
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
    planning/                     Plans 8, 12: graph_model.py validator.py inputs.py proposals.py
    execution/                    Plans 6, 8, 9, 10, 13
      graph_store.py affected.py diff_engine.py executor.py node_runner.py
      resources.py expectations.py watchdog.py recovery.py checkpointer.py (Plan 9)
    config/                       data only: models.yaml (9) resources.yaml (10) mcp_servers.yaml (14)
  application_api/                Plan 6: migrations "o" (renames) and "p" (session graph); models, repositories, schemas, routes (Appendix P)
  jobs_core/                      Plan 5 (backends/monai_http.py: Plan 15)
  compute_worker/                 Plan 5 (Celery app)
  sandbox/                        Plan 4
```

## Milestones, order and dependencies

| #   | Plan                                                      | Milestone    | Depends on         | Comes from                                                       | Status            |
| --- | --------------------------------------------------------- | ------------ | ------------------ | ---------------------------------------------------------------- | ----------------- |
| 0   | Runtime: Python 3.13, PyCaret removed                     | M0 Runtime   | –                  | your decision (this merge)                                       | 🟡 Implemented; CI not green yet |
| 1   | Toolkit Core                                              | M1 Toolkit   | –                  | tools P1; system Phase 1 (registries, artifact types, discovery) | ✅ Done (1.1–1.5) |
| 2   | Tool Packs                                                | M1           | 1                  | tools P2                                                         | ✅ Done (2.1–2.5) |
| 3   | Agent Definitions and Skills                              | M1           | 1, 2               | tools P3; system §29-30                                          | Not started       |
| 4   | Sandbox Foundation                                        | M2 Compute   | 1                  | tools P4                                                         | Not started       |
| 5   | Job Platform (Celery + Redis)                             | M2           | 1, 4               | tools P5                                                         | Not started       |
| 6 | Session State Store: Persistence Design (Application API) | M3 Harness | – (6.1 right after 0) | system Phase 1 (State, Graph Versions); Appendix P | ✅ Done (6.1–6.5) |
| 7   | Modeling Engine, Heavy Tools as Jobs + `run_modeling`     | M2           | 0, 2, 5, 6         | tools P6; system `run_modeling`; your decision (PyCaret removed) | Not started       |
| 8 | Validator and Diff Engine | M3 | 1, 6 | system Phase 1 (Validator), Phase 2 (Diff, Affected Set); Appendix P D23 | Not started |
| 9   | LangGraph Engine, Checkpoints and Model Gateway           | M3           | 1, 3, 6            | your decisions (LangGraph; LangGraph-native nodes, 2026-10-02); system §4 (Model Gateway); Appendix P D27 | Not started       |
| 10 | Executor, Node Runner, Watchdog | M3 | 5, 6, 8, 9 | system Phases 1–3 (Executor, Live changes, Watchdog); Appendix P T4–T7, T10 | Not started |
| 11  | Runtime Guardrails, Modes, Approvals                      | M3           | 4, 6, 8, 10        | system §10, §31                                                  | Not started       |
| 12  | Session Layer: Inbox, Main Loop, Context, Skills, Planner | M3           | 8, 9, 10, 11       | system Phase 1 (Main Loop, Context), Phase 2 (Planner)           | Not started       |
| 13  | Recovery and Privacy                                      | M4 Hardening | 10, 11, 12         | system Phase 3                                                   | Not started       |
| 14  | MCP Outbound Edge                                         | M4           | 1, 4, 11           | tools P7                                                         | Not started       |
| 15  | MONAI Jobs + Segmentation Client                          | M5 Job fixes | 5 (Phase 5.1 only); the review gate of 15.4 needs Phase 9.3 | tools P8                                                         | Not started       |
| 16  | Other Job Systems                                         | M5           | 5, 15              | tools P9                                                         | Not started       |
| 17  | Cut-over, AutoGen Removal, Cleanup, Docs                  | M6           | all                | tools P10; your decision                                         | Not started       |

**Progress (2026-10-01):** Plan 1 is done: all five phases, 505 toolkit tests passing on Python 3.13 (see "Plan 1 — as built" at the end of Plan 1). Plan 1 was done **before** Plan 0, so the order above was not followed. The toolkit is written for 3.13, but the agents image is still `python:3.11.3`. Until Plan 0 lands, run the toolkit tests from a local 3.13 venv: in Docker, the deterministic suite cannot collect `tests/unit/toolkit/`.

**Progress (2026-10-02):** Phase 6.1 (Cut-over 1: renames) is done: migration `o1f0a2c3d4e5_v1_o_renames` and the mechanical code rename, in commits `0b77fcb2`, `1d0958c2` and `cd7bc7e8` (see "Phase 6.1 — as built" at the end of Phase 6.1). It was also done before Plan 0. Plan 2 is next, and it moves code that already uses the Appendix P names.

**Progress (2026-10-03):** Plan 2 is done: `Backend/mcp/` is gone, the catalog loads 63 tools, and the Definition of Ready smoke test passes for every light tool (see "Plan 2 — as built" at the end of Plan 2). Like Plan 1, it was done before Plan 0, so CI (the 3.11 image) cannot collect the 3.13 tests of the toolkit and the tool packs; **Plan 0 is the recommended next plan.**

**Progress (2026-10-05):** Phases 6.2–6.5 are done, so Plan 6 is done: migration `p1f0a2c3d4e5_v1_p_session_graph`, the models, repositories and routes of T0–T10 with D24 fencing, and the Agent System's `HarnessClient` and `GraphStore` (see "Phases 6.2–6.5 — as built" at the end of Plan 6). The live database suites pass against PostgreSQL 18 (56 tests since 2026-10-06), and also in CI inside the Python 3.13 image. On 2026-10-06 the last gaps against the spec were closed: ownership on every session-scoped route, and the three integration checks that were missing (see the as-built notes).

**Progress (2026-10-06):** Plan 0 is implemented (see "Plan 0 — as built" at the end of Plan 0): the agents image is `python:3.13.16-slim` without PyCaret, the Application API image is `python:3.13-slim`, and the deterministic suite collects the toolkit and tool-pack tests in the image. The first CI run built the 3.13 image and passed `live-db-tests`, but failed to collect every module that imports an AutoGen agent (mcp 2 removed an import `autogen_ext` needs; now `mcp>=1.17,<2`), and the radiology job could not pull the 11 GB MONAI CPU image (it now frees disk first). Mark Plan 0 done once `backend-tests.yml` passes.

**Decision (2026-10-02): LangGraph-native agent nodes.** Plan 9 now also builds the checkpointer, gates, `@task` tool calls, `Send` fan-out, the literature workflow graph and in-node compaction; Appendix P gains D27 (agent checkpoints, suspended attempts, transaction T10) and becomes revision 3.2; Plans 3, 6, 8, 10–17 carry the matching edits. The implementation order does not change: Plan 9 already comes after 6.2–6.5, which now include the checkpoint tables and routes.

**Implementation order:** 0 → 1 ✅ → 6.1 ✅ → 2 → 3 → 4 → 5 → 6.2–6.5 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 15 → 16 → 17.

Phase 6.1 (the renames of Appendix P) comes right after Plan 0 and before Plan 2. The tool move in Plan 2 then moves code that already uses the new names, and no later plan writes an old name. The rest of Plan 6 comes before Plan 7, whose tools write `model_results` through the new fencing.

Plan 0 comes first so that every later plan is written and tested on Python 3.13. From Plan 0 until Plan 7, the moved `pycaret_*` tools cannot run (PyCaret is no longer installed). This is allowed by the global rule that the project does not need to run between plans.

Some plans can move:

- Phases 6.2–6.5 touch only the Application API and its client facade, so they can start any time after 6.1.
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

## Plan 0 — as built (2026-10-06)

- **Images:** `deploy/Dockerfile.agents` is `FROM python:3.13.16-slim` (3.13.16 was the newest patch on Docker Hub; Phase 0.1 named 3.13.15), PyRadiomics is pinned to `@8ed5793`, and the step that reinstalled `numpy==1.26.4 pandas==2.1.4 pycaret==3.3.2 shap` last is gone. The new base is Debian 13, so the `archive.debian.org` redirect the 3.11.3 (Debian 11) base needed is removed with it. `deploy/Dockerfile.application-api` is `FROM python:3.13-slim` and no longer installs `pydicom==2.4.4`, which no Application API module imports.
- **Requirements:** `numpy>=2.3,<3`; `joblib`, `lightgbm`, `xgboost` and `shap` added; still UTF-16 LE with CRLF. `Backend/requirements/test.txt` pins `sqlalchemy>=2.0,<2.1`, the range the Application API image ships.
- **`.python-version`:** `3.13.16`.
- **Smoke test:** `Backend/tests/runtime/test_image_smoke.py` (marker `docker`) covers the four checks of Phase 0.2. It skips outside a container (`/.dockerenv`), and the CI step fails if it skipped. It runs in `deterministic-tests`, inside the image built from the pushed commit.
- **CI:** there is no `docker-build-agents.yml` any more; `backend-tests.yml` builds the agents image per commit (`build-image.yml`), and the separate 3.13 `toolkit-tests` job is folded into `deterministic-tests`. `PY313_ONLY_TESTS` in `Backend/conftest.py` stays for local interpreters older than 3.13.
- **Not done locally:** the Phase 0.2 resolve check (`uv pip compile`) and the image build. The first CI run is that check.
- **Still pending:** the `pycaret_*` tools raise `ImportError` until Plan 7 rewrites them, as Plan 0 allows.

---

# Plan 1 — Toolkit Core ✅

**Goal:** a framework-neutral core for tools, agents, artifact types, context, events, dispatch and delegation.

**What changed from tools_plan Plan 1:**

- **Added:** the manifest fields, ports, artifact types, visibility sets, the compact index and the agent-as-tool factory.
- **Moved out:** `RunHarness`, `node_resolver` and `DirectToolExecutor` go to Plan 10. The `CANONICAL_AGENT_TYPES` contract is replaced (see Plan 6).

## Phase 1.1 — Contracts ✅

1. **`toolkit/tools/spec.py` — `ToolSpec`:**

   | Field                                                      | Meaning                                                                                                                                         | From                                    |
   | ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- |
   | `name`, `version`, `description`, `pack`                   | Identity. Packs: population, db_reads, segmentation, reporting, bookkeeping, literature, modeling, radiomics, delegation, control, external_mcp | both                                    |
   | `handler`                                                  | An async function (first-party), or a closure (external)                                                                                        | tools                                   |
   | `input_model` / `input_schema`                             | The params model built from the signature, or a raw JSON schema (external)                                                                      | tools                                   |
   | `inputs`                                                   | Ports filled from upstream artifacts: `{port: PortSpec(type, param)}`. `param` names the handler argument that receives the artifact ref.       | system manifest `inputs`                |
   | `outputs`                                                  | Ports the tool produces: `{port: PortSpec(type, extract)}`. `extract(result)` returns the artifact ref and its summary.                         | system manifest `outputs`               |
   | `output_model`                                             | Optional result model                                                                                                                           | tools                                   |
   | `side_effects`                                             | `none` \| `writes` \| `compute`                                                                                                                 | system §16                              |
   | `cost`                                                     | `light` \| `medium` \| `heavy`                                                                                                                  | system §16                              |
   | `resources`                                                | Named pools: `llm`, `cpu_heavy`, `gpu`, `highmem`, `monai_inference`, `orthanc_io`                                                              | system; replaces tools `resource_class` |
   | `execution`                                                | `inline` \| `job` \| `external`                                                                                                                 | tools                                   |
   | `trust`, `isolation`, `data_scopes`                        | As in tools_plan (`TrustLevel`, `IsolationLevel`, `DataScope` enums live here)                                                                  | tools                                   |
   | `phi`                                                      | Derived: true when `data_scopes` contains `phi_read` or `phi_write`                                                                             | system                                  |
   | `ui`                                                       | The view type for the node, e.g. `modeling_results_view`                                                                                        | system                                  |
   | `on_result`                                                | Hook: result → UI view events                                                                                                                   | tools                                   |
   | `visibility`                                               | A subset of {`main`, `graph`, `agent`, `harness`}                                                                                               | merged                                  |
   | `timeout_s`, `queue_timeout_s`, `priority`, `max_attempts` | Budgets and the retry cap                                                                                                                       | tools + system Watchdog                 |
   | `read_only`, `destructive`, `idempotent`, `critical`       | As in tools_plan                                                                                                                                | tools                                   |

   `resource_class` (the Celery queue) is a derived property: `cpu_heavy`→`compute.cpu`, `gpu`→`compute.gpu`, `highmem`→`compute.highmem`.

2. **Manifest consistency rules** (enforced in Phase 1.3):
   - `side_effects="none"` ⇔ `read_only=True` and no write scope (`phi_write`, `db_write`, `fs_write`);
   - `cost="heavy"` ⇒ `execution="job"`;
   - `execution="job"` ⇒ `resources` is not empty, and the args are JSON only;
   - `"main"` in `visibility` ⇒ `execution="inline"` and `cost` is not `heavy`. Most main tools are `light`. `plan` and `recover` are `medium`, with a hard timeout. Together with the `REQUIRES_NODE` guardrail, this enforces "Main never runs long work".
   - `"graph"` in `visibility` ⇒ at least one output port, and every input port's `param` exists in `input_model`.
3. **`toolkit/ports.py`:** `PortSpec(artifact_type, param=None, many=False, required=True, extract=None)`.
4. **`toolkit/agents/spec.py` — `AgentSpec`.** It is a **separate class and does not inherit from `ToolSpec`**.

   | Field                                                               | Meaning                                                                                                                                              |
   | ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
   | `agent_type`, `version`, `description`, `kind`                      | `kind` is one of: `control` (main_agent, composer, recovery_agent); `execution` (can be a graph node); `internal` (reached only through `run_agent`) |
   | `system_prompt`                                                     | Text from `agent.md`, or a function of the context                                                                                                   |
   | `allowed_tools`                                                     | A list of names, or a function of the context (for example, "all tools with `main` visibility")                                                      |
   | `delegates`                                                         | The internal agents it may start with `run_agent`                                                                                                    |
   | `inputs` / `outputs`                                                | Ports (execution agents only)                                                                                                                        |
   | `model_profile`                                                     | A Model Gateway profile (Plan 9)                                                                                                                     |
   | `max_tool_iterations`, `timeout_s`, `resources` (default `["llm"]`) | Budgets                                                                                                                                              |
   | `completion_check`, `max_completion_retries`, `output_contract`     | Completion rules                                                                                                                                     |
   | `context_builder`                                                   | Builds the agent's first message from the node inputs (Plan 12)                                                                                      |
   | `ui`, `human_in_the_loop`, `parent`                                 | The view type; whether the agent has user gates inside the node; the owner of an internal agent                                                      |

   These tools_plan fields are removed, because `kind` and `visibility` replace them: `planner_visible`, `phase`, `bootstrap`.

5. **`toolkit/context.py` — `ToolContext`:**
   - Fields: `session_id`, `agent_run_id` (the run row of the current node), `node_id`, `graph_version`, `step_id` (legacy, nullable), `agent_type`, `caller` (`main|agent|node|harness`), `subagent`, `depth`, `workspace_dir`, `tool_name`, `job_id`, `execution_mode`, `privacy_mode`, `event_sink`, `session_handle`, `mailbox`. _Phase 6.1 ✅ renamed `agent_run_id` → `run_id`, added `attempt_id`, and removed `step_id` and `graph_version`._
   - It is stored in a `ContextVar`, read with `current_context()`, and serialized with `to_payload()` / `from_payload()`. The payload leaves out `event_sink`, `session_handle` and `mailbox`.
6. **`toolkit/events.py`:**
   - Move `ToolEvent`, `EventType` and the payload classes here from `Backend/mcp/tool_logging.py`, and add an `EventSink` protocol.
   - **Rule:** `source` = the node's ref name (an agent type or a tool name), or `main_agent`. This keeps `PlaygroundCanvas.tsx:145-167` working.
   - New optional fields: `tool_name`, `subagent`, `job_id`, `node_id`.
7. **`toolkit/results.py`:**
   - `error_result(code, message, **extra)` → `{"status":"error","error_code","error_message",...}`;
   - `RunAccepted {node_id, run_id | None, status, graph_version, approval_id | None}`. _Phase 6.1 ✅ changed it to `{node_id, change_id, status}`._

## Phase 1.2 — Artifact types (`toolkit/artifacts/`) ✅

1. **`base.py`:**
   - `ArtifactType(name, version, ref_model, summary_model, phi_default, parents)` and an `ArtifactTypeRegistry`;
   - `is_compatible(produced, expected)`: the names are the same, or `expected` is one of the produced type's `parents`.
2. **`builtin.py`:**

   | Type                | Ref (what it points to)                     | Summary (read by expectations and by Main's context) |
   | ------------------- | ------------------------------------------- | ---------------------------------------------------- |
   | `Cohort`            | `cohort_id` (`cohorts`)                     | `size`, `label_counts`, `frozen`                     |
   | `ImageSet`          | `series_uids`                               | `n_series`, `modalities`                             |
   | `MaskSet`           | `segmentation_run_id` (`segmentation_runs`) | `n_masks`, `n_failed`, `review_status`               |
   | `FeatureTable`      | `feature_set_id` (`feature_sets`)           | `n_rows`, `n_features`                               |
   | `FeatureImportance` | `feature_set_id`, `method`                  | `top_features`                                       |
   | `ModelingOutput`    | `model_result_ids` (`model_results`)        | `metric_name`, `best_metric`, `n_models`             |
   | `LiteratureContext` | `literature_review_id`                      | `n_papers`                                           |
   | `ResultSummary`     | `agent_run_id` (`output_summary`)           | `n_findings`                                         |
   | `ReportDocument`    | `report_id` (`reports`)                     | `source_version`, `has_pdf`                          |

   _Phase 6.1 ✅ renamed two refs: `MaskSet` → `segmentation_batch_id` (`segmentation_batches`), and `ResultSummary` → `run_id` (`scratchpad_summary`)._

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

| Phase | Commit(s)                                      | What landed                                                                                                                               |
| ----- | ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| 1.1   | `9fa8afc8`, `494392e8`, `db187326`, `062c1fbb` | `manifest.py`, `tools/spec.py`, `tools/consistency.py`, `ports.py`, `agents/spec.py`, `context.py`, `events.py`, `results.py`             |
| 1.2   | `0c1f942e`                                     | `artifacts/base.py`, `artifacts/builtin.py` (the 9 built-in types)                                                                        |
| 1.3   | `5c7fc780`, `adea7767`                         | `registry.py` (generic base), `tools/registry.py` (`@tool`), `agents/registry.py`, `catalog.py`                                           |
| 1.4   | `65586537`, `82fd5af6`, `717641c3`             | `runtime/{cancel,inline,dispatch,session_sink}.py`, `agents/engine.py`, `delegation/run_agent.py`, `delegation/agent_as_tool.py`          |
| 1.5   | not committed yet                              | `tests/support/toolkit_fixtures.py`, `tests/unit/toolkit/conftest.py`, markers, `LEGACY_AUTOGEN_TESTS` auto-marking, deterministic filter |

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
- ✅ **Pinned again (2026-10-06): `mcp>=1.17,<2`.** Plan 2 deleted the FastMCP server, but the frozen AutoGen agents import `autogen_ext.tools.mcp`, which imports `mcp.shared.context.RequestContext`; mcp 2 removed it, and the first fresh image build (Plan 0) failed to collect every module that imports an AutoGen agent. The cap goes with AutoGen in Plan 17; Plan 14 pins the version its outbound edge needs.
- The LangGraph-native decision (2026-10-02) extends four Plan 1 contracts. The work is done in **Phase 9.3**, not by reopening Plan 1, and keeps `toolkit/` free of LangGraph imports: `Dispatcher(passthrough=…)`, `ToolContext.user_gate()` / `ToolContext.durable()`, `AgentStatus.SUSPENDED` with `AgentResult.suspension`, and `AgentEngine.run(..., resume=…)`.
- ✅ **Done in Phase 6.1 (2026-10-02).** Phase 6.1 renamed the context and result fields that Plan 1 built on the old names: `ToolContext.agent_run_id` → `run_id`; `step_id` and `graph_version` removed; `attempt_id` added (the D24 fence). `RunAccepted` is `{node_id, change_id, status}`, with no `run_id`, because a run is created only when its node starts (D8). The built-in artifact refs are `MaskSet.segmentation_batch_id` and `ResultSummary.run_id`. The toolkit suite now has 503 passing tests and 1 skipped (3 parametrized cases of the removed fields are gone).

---

# Plan 2 — Tool Packs: move every tool and make the light tools ready ✅

**Goal:** `Backend/mcp/` is gone. All tools live in `agent_system/toolpacks/<pack>/` and are registered with `@tool` plus a full manifest. They read identity from the context, and the light tools pass the Definition of Ready.

## Phase 2.1 — The big move (one rename commit, then one edit commit) ✅

1. **`git mv`:**

   | From (`Backend/mcp/...`)                                                                             | To (`Backend/agent_system/toolpacks/...`)                                                                          |
   | ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
   | `tools/helpers/*`, `service_urls.py`                                                                 | `common/`                                                                                                          |
   | `tools/modeling/*`                                                                                   | `modeling/` (moved as they are, still importing PyCaret; they cannot run after Plan 0 and are rewritten in Plan 7) |
   | `tools/feature_extraction/*`                                                                         | `radiomics/`                                                                                                       |
   | `tools/population/*`, `tools/save_population.py`, `tools/targets_matcher.py`                         | `population/`                                                                                                      |
   | `medical_report/*`                                                                                   | `population/data/`                                                                                                 |
   | `tools/database/**`, `tools/get_patients_info.py`, `tools/get_studies_info.py`                       | `db_reads/`                                                                                                        |
   | `tools/run_segmentation.py`, `tools/check_having_mask.py`, `tools/list_models.py`                    | `segmentation/`                                                                                                    |
   | `tools/latex/*`, `tools/get_modeling_results.py`                                                     | `reporting/`                                                                                                       |
   | `tools/literature_context.py`                                                                        | `literature/`                                                                                                      |
   | `tools/scratchpad.py`, `tools/agent_output.py`, `tools/task_context.py`, `tools/browse_workspace.py` | `bookkeeping/`                                                                                                     |
   | `test_data/`                                                                                         | `Backend/tests/fixtures/sample_data/`                                                                              |

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

## Phase 2.2 — Common helpers ✅

In `toolpacks/common/`:

- one shared `httpx.AsyncClient` with explicit timeouts;
- lazy, validated settings (in the style of `timeout_policy._positive_float`);
- `domain_api.session_id()` / `run_id()` / `attempt_id()` read `current_context()`. Every write call sends the D24 write path (`X-Attempt-Id`, or `X-Run-Id` for an internal job) so the Application API can set it for the trigger;
- `WorkspacePath` resolves against the context at validation time;
- one `app_api_url()`, which removes 4 copies;
- remove the `sys.path` hack at `list_models.py:7-12`.

## Phase 2.3 — Context migration (all packs, including the identity reads in the heavy files) ✅

- **Pattern A (the value is copied at import):** `agent_output.py:50-52`, `scratchpad.py:30-35`, `task_context.py:25-30`, `browse_workspace.py:3,11,66`, `search_volumes.py:10` (unused, so delete it), `regression.py:24`.
- **Pattern B (the env var is read at call time):** `targets_matcher.py:35`, `inclusion_criteria.py:18`, `validate_anatomy.py:70`, `cohort_labels.py:186,345`.
- The LLM settings in `cohort_labels.py` (`MODEL_BASE_URL`, `MODEL_NAME`, `MODEL_API_KEY`, `LLM_CONCURRENCY_LIMIT`) move to the Model Gateway `extraction` profile in Plan 9. Until then, read them from `client_config.py`.

## Phase 2.4 — Light tool fixes and manifests ✅

1. **Fixes:** fix L1–L14 (Appendix D) and the `generate_targets` wait S12. The S12 fix uses `poll_until`, which lands in Phase 5.1; until then, write it against the same signature.
2. **A manifest on every light tool:** `pack`, `execution="inline"`, `trust="first_party"`, `isolation="inline"`, `side_effects`, `cost`, `resources`, `data_scopes`, `timeout_s` and `visibility`:

   | Group                             | `visibility`                   | Examples                                                                                                                                  |
   | --------------------------------- | ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
   | Read-only reads                   | {`main`, `agent`}              | the 14 DB tools, `get_patients_info`, `get_studies_info`, `check_having_mask`, `list_models`, `browse_workspace`, `get_modeling_results`  |
   | Domain actions used inside agents | {`agent`}                      | population tools, `generate_targets`, `write_agent_output`, scratchpad reads and notes, `latex_*` except `latex_to_pdf`                   |
   | Actions that can be graph nodes   | {`graph`, `agent`}, with ports | `run_segmentation_with_guard` (`cohort: Cohort` → `masks: MaskSet`), `latex_to_pdf` (`report: ReportDocument` → `report: ReportDocument`) |
   | Run bookkeeping                   | {`harness`}                    | `scratchpad_register_agent`, `scratchpad_set_status` (called by the Node Runner)                                                          |

   An LLM-backed tool such as `extract_cohort_labels` gets `cost="medium"` and `resources=["llm"]`.

3. **`run_segmentation_with_guard`:** register it in `toolpacks/segmentation/`. **Copy** the logic of `segmentation_tool()` from `segmentation_agent.py:61-134`; do not edit the AutoGen file. It gets `critical=True` and one name (L13).
4. **Literature search tools:** copy them from `literature_review_agent.py:218-520,1499-1525` into `toolpacks/literature/search.py`, **without** editing the frozen file:
   - `search_pubmed`, `search_arxiv`, `search_semantic_scholar`, `search_europe_pmc`, `score_paper_relevance`;
   - `save_literature_output`, which was `save_output`. This is the only new name: `save_output` is too generic for a global registry.

   They use the shared httpx client, `data_scopes={"network_egress"}` and `visibility={"agent"}`.

5. **`on_result` hooks** for `write_agent_output`, `generate_targets`, `get_patient`, `get_volume`, `get_modeling_results` and `latex_to_pdf`. **Copy** them from `pipeline_monitor.py:130-280`; the legacy monitor stays frozen.

## Phase 2.5 — Definition of Ready check ✅

A smoke test runs every tool with a fake context and a mocked domain API. It checks the error envelope and the manifest rules. It also checks that no identity env var is read (by monkeypatching `os.environ`).

**Tests:**

- `tests/unit/toolpacks/light/`: one success test and one error test per tool;
- golden tests showing that each moved `on_result` hook produces the same view event as the legacy monitor;
- the literature tools against `respx` mocks.

**Done when:**

- `Backend/mcp/` no longer exists, and no `@mcp.tool` / `FastMCP` remains;
- the catalog loads **63 tools**: 62 pack tools (55 moved + `run_segmentation_with_guard` + 6 literature tools), plus `run_agent` from Plan 1;
- the DoR smoke test passes for all light tools.

## Plan 2 — as built (2026-10-03)

**Status:** done. The catalog loads 63 tools (55 moved, `run_segmentation_with_guard`, the 6 literature tools, `run_agent`). `tests/unit/toolpacks/` has 247 tests and `tests/unit/toolkit/` 504, all passing on Python 3.13; `tests/unit/toolpacks/light/test_definition_of_ready.py` checks every light tool.

| Phase | Commit(s)                                         | What landed                                                                                                                                                                         |
| ----- | ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2.1   | `64b5081f` (renames), `e3b538af`, `fdec0d88`      | `git mv` into `toolpacks/<pack>/`; `@tool` with an interim manifest; `toolpacks/_toolkit.py`; `Backend/mcp/` and `classification_tool.py` deleted; the event calls on `emit()`/`emit_view()` |
| 2.2   | `cf4ad6e6`                                        | `common/settings.py`, `common/service_urls.py` (`app_api_url()`), `common/http_client.py` (shared client, D24 headers), identity from the context in `domain_api`, `WorkspacePath` on the context |
| 2.3   | `d77d3f8d`                                        | Pattern A and B identity reads replaced by the context; `common/llm.py` (the extraction LLM from `client_config`)                                                                  |
| 2.4   | `7aa9b7b0`, `a7b24c0f`, `050cf586`, `6fbfbdd6`, `f7f31bb0`, `763df578` | L1–L12 and S12 (`common/polling.py`); `run_segmentation_with_guard`; the 6 literature tools; full manifests and ports; the `on_result` hooks (`common/views.py`)  |
| 2.5   | `5cafabf6`                                        | Every light-tool failure is an `error_result`; the Definition of Ready smoke test                                                                                                  |
| docs  | `64bc5744`                                        | `CLAUDE.md` ("Tool packs" conventions), `cubeai-docs`, `deploy/.env.example`, comments                                                                                             |

**Differences from the plan text** (later plans should build on these):

- **Layout:**
  - `tools/database/**` became `db_reads/` without the `database/` level (`db_reads/patients/`, `db_reads/volumes/`, ...); `tools/latex/*` became `reporting/`, and `tools/helpers/*` became `common/`.
  - `toolpacks/_toolkit.py` re-exports the toolkit under the import root the packs were loaded with (`agent_system.toolkit` in the tests, bare `toolkit` in the container). A pack that imported the other root would build its `ToolSpec` from a second copy of the class and drop out of the catalog. **Packs import the toolkit only through `_toolkit`.**
  - `common/` also holds `manifests.py` (`READ_ONLY`, `WRITES`, `INTERIM_HEAVY`), `emit.py`, `polling.py`, `views.py` and `llm.py`; `db_reads/_api.py` is the read helper of the DB tools.
- **Phase 2.1 in three commits, not two:** the renames; the edit, with a compatibility shim that kept the old `log_tool_state` / `broadcast_ws_message` names; then the 77 event calls rewritten to `emit()` / `emit_progress()` / `emit_view()` and the shim deleted. The `sys.path` hack of `list_models.py` (a Phase 2.2 item) was removed in 2.1, because the move changed what it pointed at.
- **`ToolSpec` needs four manifest fields** (`side_effects`, `cost`, `execution`, `visibility`), so every tool got a minimal manifest in 2.1 and the full one in 2.4. **`INTERIM_HEAVY`** (`cost="medium"`, `execution="inline"`) marks the 9 heavy tools until Plan 7: radiomics ×2, the 5 PyCaret tools, `run_segmentation`, `run_segmentation_with_guard`.
- **Events:** dispatch sends `tool_started` and `tool_finished` / `error` for every call, so the tools no longer send them; their own messages became `tool_log`. A tool's view updates come from its `on_result` hook. `emit_view(view_type, payload)` has no `session_id` argument: the session is the context's.
- **Identity and HTTP:**
  - `domain_api.session_id()`, `run_id()` (was `bound_run_id()`), `attempt_id()`, `node_id()`, `agent_type()` read the context; `require_run()` / `require_session()` raise when it has none.
  - Every Application API write sends `X-Attempt-Id` when the context has an attempt, else `X-Run-Id` when it is a job's (`job_id` and `run_id`), else nothing; nothing is refused on the client side (Plan 6's fence decides).
  - `DomainApiError` wraps an unreachable API too (`status_code=None`), and `error_code` / `error_result_from()` map it to `unavailable`, `not_found`, `conflict`, `bad_request` or `upstream_error`. A missing population cohort is a 404.
  - The shared client is one `httpx.AsyncClient` per event loop. `aclose_shared_client()` exists; the app lifespan does not call it yet (Plans 10 and 12).
- **`WorkspacePath`** resolves against `ctx.workspace_dir`, else the session's workspace from `storage_paths`; outside a dispatched call it is invalid.
- **Fixes on the way (beyond L1–L14):** `search_volumes_by_description` writes the cohort's selection summary, so it is `WRITES` and `{agent}` only; `get_invalidate_segmentations` calls `/segmentation-masks/unvalidated/`; `get_all_patients_info` / `get_all_studies_info` take `skip` / `limit`; `list_models` returns the list itself (L11), which the model sees as the same JSON; `latex_to_pdf` returns `error_result` (it is a graph tool); a failed segmentation is `error_result("segmentation_failed", ...)` after its output is recorded, so `critical=True` fails the node as `CRITICAL_PIPELINE_TOOLS` did.
- **`run_segmentation_with_guard`** takes no identity argument; its only argument, `cohort_id`, receives the `Cohort` input port, and any cohort other than the session's population cohort is refused. Its `MaskSet` output derives `review_status`: `submitted` when the batch has ready masks (the module returns only once their review is submitted), else `not_ready`.
- **Literature tools:**
  - They run on the shared async client; the synchronous `urllib` and `time.sleep` delays are gone, the retry rule (429/5xx, three attempts) is kept.
  - The closures' state is per run in `literature/run_state.py`: `use_pico(run_id, pico)` sets the domain keywords (else the broad fallback list), `save_literature_output` captures into memory, `release(run_id)` hands the files over and forgets them.
  - `extract_concepts_from_pico` and its term dictionaries are copied into `literature/pico.py`.
  - A failed search is an `error_result` that keeps `source`, `query` and `results=[]`.
- **`on_result` hooks** copy the legacy monitor's branch order exactly (`_branch()`), with one fix: its `_read_agent_output()` read `step.get("position")`, a NameError since Phase 6.1; the copy reads the node.
- **Known exceptions to the Definition of Ready:** item 4 (module-level state) — `targets_matcher._announced_sessions` (L7's per-session state) and `literature/run_state.py`; items 13 and 15 wait for Plans 9–11; the heavy tools wait for Plan 7.
- **Tests:** `tests/support/mcp_loader.py` and `tests/unit/mcp/` are deleted; `test_data/` is `tests/fixtures/sample_data/`. The tool-pack tests use `fake_domain_api` (respx) and `call_tool`; the golden view tests need `aiofiles` (`importorskip`).

**Open follow-ups:**

- **Plan 0 first.** The 3.11 image (CI) cannot collect `tests/unit/toolkit/`, `tests/unit/toolpacks/`, `tests/unit/functions/` or the contracts that import `agent_system.toolpacks`. Until then, run them on a local 3.13 interpreter (`CLAUDE.md`, Commands).
- **The legacy AutoGen pipeline cannot run its tools:** its agents still start the deleted `Backend/mcp/server.py`. Allowed by the global rules until the cut-over (Plan 17).
- **Phase 9.7:** `SegmentationModule.initialize()` also starts `Backend/mcp/server.py`, so `run_segmentation_with_guard` cannot run until the module uses the agent engine. The module could then return the batch's `review_status` itself instead of the derived one.
- **Plan 3:** the literature agent's `context_builder` calls `literature.run_state.use_pico(run_id, pico)`, and its output hook calls `release(run_id)` and records the review (`create_literature_review`); a run that is never released stays in memory. The `agent.md` prompts take the L14 fixes (argument names, the agent id; register/status are already `harness`-only).
- **Phase 5.1:** move `toolpacks/common/polling.py` (`poll_until`, `RetryPolicy`, `PollTimeout`) to `jobs_core/polling.py` and import it from there. `generate_targets` (S12) is its first user.
- **Plan 7:** replace `INTERIM_HEAVY` on the 9 heavy tools, and move their view branches (radiomics, PyCaret) out of the legacy monitor into `on_result` hooks.
- **Plans 10–12:** call `aclose_shared_client()` from the app lifespan, and move `targets_matcher._announced_sessions` and `literature/run_state.py` to the runtime that owns per-run state.
- **Plan 17.3:** `/events` and `/ws-broadcast` have no producer in the repository any more (only `tests/manual/view_payload_harness.py`).

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
   resumable: true
   gates: [segmentation_review]
   gate_timeout_s: null
   max_context_tokens: 60000
   ---
   <system prompt>
   ```
   **LangGraph-native fields** (decision 2026-10-02, used by Plan 9):
   - `resumable` (default `true` for `execution`, `false` for `control` and `internal`): the node runs with `HttpCheckpointSaver`, and a retry attempt resumes the run's last checkpoint (D27);
   - `gates`: the gate names the agent's tools may raise with `ctx.user_gate()` (`cohort_approval`, `segmentation_review`; `tool_approval` is implicit). `human_in_the_loop` is derived: true when `gates` is not empty;
   - `gate_timeout_s` (default `null`, no limit): how long a suspended gate may wait before the Watchdog cancels it. It is separate from `timeout_s`, which counts only running time;
   - `max_context_tokens`: above it, the engine's `compact` node summarizes older messages (Phase 9.4);
   - `workflow` (optional): a list of `internal` agents that are steps of a fixed graph built by `hooks.build_graph` (Phase 9.6). An agent with `workflow` has no `delegates` and no `run_agent`.
2. **Hooks:** an optional `hooks.py` next to the file exports `completion_check`, `context_builder`, `allowed_tools_fn`, the output-port `extract` functions, and `build_graph(ctx) -> StateGraph` for a `workflow` agent.
3. **`toolkit/agents/loader.py`:** reads the front matter with `yaml.safe_load`, validates it with a Pydantic model, attaches the hooks and returns an `AgentSpec`. `catalog.build()` calls it for every `agent.md` file under `agentpacks/`.

## Phase 3.2 — Execution agents (6)

| Agent                            | Source (copy from)                   | Inputs                                                               | Outputs                                                              | Notes                                                                                                                                                                    |
| -------------------------------- | ------------------------------------ | -------------------------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `study_population_planner_agent` | `population_agent.py:128-222`        | – (params: criteria text)                                            | `cohort: Cohort`                                                     | Conditional `extract_cohort_labels` via `allowed_tools_fn`; `completion_check` = `services/population_handoff.py:34-78`, 2 retries; cohort-approval gate inside the node (`gates: [cohort_approval]`) |
| `segmentation_agent`             | `segmentation_agent.py:156-272`      | `cohort: Cohort`                                                     | `masks: MaskSet`                                                     | Delegates the 2 segmentation internal agents; review gate inside the node (`gates: [segmentation_review]`)                                                                |
| `feature_extraction_agent`       | `feature_extraction_agent.py:47-132` | `cohort: Cohort`, `masks: MaskSet`                                   | `features: FeatureTable`, `importance: FeatureImportance` (optional) | Calls the radiomics job tools                                                                                                                                            |
| `result_reporter_agent`          | `result_reporter_agent.py:52-106`    | `modeling: ModelingOutput`, `features: FeatureTable` (optional)      | `summary: ResultSummary`                                             | Uses its real id (not `"result_agent"`)                                                                                                                                  |
| `latex_reporter_agent`           | `reporter_agent.py:49-105`           | `summary: ResultSummary`, `literature: LiteratureContext` (optional) | `report: ReportDocument`                                             |                                                                                                                                                                          |
| `literature_review_agent`        | `literature_review_agent.py`         | – (params: question / PICO)                                          | `literature: LiteratureContext`                                      | A `workflow` agent: its 5 internal agents are the steps of a fixed graph (Phase 9.6), not `run_agent` delegates                                                         |

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

  All are copied from `literature_review_agent.py:1528-1560`. The parent lists them under `workflow` (in this order), not under `delegates`: its `hooks.build_graph` wires them as `pico_analyst → query_builder → search_executor (Send per source) → relevance_ranker (Send per paper) → context_compiler` (Phase 9.6). `search_executor` and `relevance_ranker` keep their `agent.md` for the per-item prompt and tools; one item is one fan-out branch.

- **`catalog.validate()` additions:** every `workflow` entry is `internal` with this agent as `parent`; an agent has `workflow` or `delegates`, never both; a `workflow` agent has `build_graph`; every name in `gates` is a known gate (`cohort_approval`, `segmentation_review`) and only `execution` agents have gates.

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

   | Skill                  | Content copied from                                                                                                                                                                                                       |
   | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
   | `research_study`       | The HypothesisArchitect and PipelineArchitect prompts in `planning_agents.py` (PICO; the typical radiomics flow)                                                                                                          |
   | `modeling`             | The `modeling_agent.py:36-99` prompt: choosing the target, task type, comparisons and folds, rewritten as advice on `run_modeling` params and the engine's estimator ids (Plan 7). No PyCaret names or PyCaret functions. |
   | `cohort_building`      | The population agent prompt (criteria, labels, cohort size)                                                                                                                                                               |
   | `segmentation_choices` | The segmentation prompt (model families, review)                                                                                                                                                                          |
   | `reporting`            | The result and LaTeX reporter prompts                                                                                                                                                                                     |

3. **Rule:** a skill is guidance text. It never routes and never forces a workflow.

**Tests (`tests/unit/agentpacks/`):**

- loader errors (bad front matter, unknown port type, missing prompt);
- each spec's allowlist, iterations and ports; the conditional tool; the `completion_check` wiring;
- the LangGraph-native fields: `resumable` defaults per kind; `human_in_the_loop` derived from `gates`; an unknown gate, a gate on a non-execution agent, `workflow` together with `delegates`, and a `workflow` agent without `build_graph` are refused;
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
     job_id:  sha256(run_id | job_name | canonical_json(args))   # run_id = the node run (Plan 6) -> resume re-attaches
     submission_key: f"{job_id}.{attempt_number}"              # = run_attempts.submission_key (Appendix P)
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

- uses `redis.asyncio`; the record of one attempt is in `cubeai:job:{submission_key}` (a hash) and the index in `cubeai:jobs:run:{run_id}`, so reconciliation (T5) finds exactly that attempt;
- dedupes with `SET NX`; the same id with a different `input_hash` → a conflict error;
- `send_task("compute.run_tool_job", task_id=submission_key, queue=f"compute.{resource_class}", expires=queue_timeout_s)`;
- never creates an attempt on its own: a retry is a new `run_attempts` row opened by T4, which arrives here as a new `submission_key`. Resubmitting the same key (T5) returns the existing record;
- `cancel` = a Redis cancel flag + `revoke(terminate=True)`;
- a terminal record gets its TTL (`JOB_RETENTION_HOURS`, default 24) only when `purge` is called after the Application API recorded the outcome (T6). Until then it never expires, so "record not found" keeps its meaning (Appendix P §P.9, Phase 3).

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
   1. Check the cancel flag and the queue timeout, then move the attempt to `running` through the Application API (`POST /runs/{run_id}/attempts/{attempt_id}/running`). A refusal (the attempt is no longer the run's active one; H20) ends the job as `run_not_running` without starting the child.
   2. Mark the job `RUNNING` with its host and pgid.
   3. Pick a runner with `select_runner(isolation)` and launch the child.
   4. Loop: relay `progress.json` to the record, refresh the heartbeat, watch the cancel flag, and enforce the timeout with `killpg`.
   5. On exit: read `result.json`. Store the result in Redis if it is ≤ `JOB_RESULT_MAX_BYTES`; otherwise keep a file ref in the job dir. The final status is `SUCCEEDED`, `FAILED`, `TIMED_OUT` or `CANCELLED`.
   6. **OOM detection:** a child that reports `MemoryError`, or that dies by SIGKILL / exit code 137 near its memory limit, is recorded as `FAILED` with `error_code="oom"`.
4. **`child.py`:**
   - calls `ensure_storage_layout()` and `install_write_guard()`, and sets up a per-job log handler;
   - applies thread caps (`OMP_NUM_THREADS`, `COMPUTE_CPU_THREADS`);
   - builds `ToolContext.from_payload()` with a `FileProgressSink`. The payload carries `run_id` and `attempt_id`, so every write the handler makes presents the D24 attempt path;
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
  - one submission key per attempt; resubmitting the same key returns the record; `expires` → `TIMED_OUT(queue)`; the TTL starts only after `purge`;
  - retry policy / `Retry-After`; pause-aware deadline;
  - supervisor with a fake runner; result size → file ref; OOM mapping; liveness key refresh.
- **`tests/integration/jobs/`** (`-m "integration and celery"`, with real Redis and a real worker):
  - submit → progress → result;
  - cancel kills the process group (a fake handler that sleeps and starts a child);
  - soft and hard timeouts;
  - worker restart → `INTERRUPTED`; the stale-heartbeat sweep;
  - `run_not_running`; a write with a stale `attempt_id` is rejected; the write guard is active in the child.

**Done when:** the unit and integration job suites pass, and `runtime/job.py` works with both the in-memory and the Celery backends.

---

# Plan 6 — Session State Store: Persistence Design (Application API) ✅

**Goal:** implement Appendix P (the persistence design, revision 3.2): the database, the transactions T0–T10 and the HTTP surface the harness uses. The Application API owns every row, and the Agent System reaches it only over HTTP. Appendix P is the specification; this plan says in which order it is built and how it is tested, and never restates its column lists.

**Order inside the roadmap:** Phase 6.1 lands right after Plan 0 and before Plan 2. Phases 6.2–6.5 land before Plan 7.

## Phase 6.1 — Cut-over 1: renames (migration `o1f0a2c3d4e5_v1_o_renames`, `down_revision = n1f0a2c3d4e5`) + the mechanical code rename, in one commit ✅

1. **The migration** follows Appendix P §P.9 *Cut-over 1*, steps 1–7:
   - the 8 table renames and every column rename of the rename map (§P.2);
   - constraint and index names; re-created trigger functions;
   - the `correlation_run_id` → `batch_correlation_id` key and the `retention_class` values.

   `downgrade()` reverses every rename.
2. **The same commit renames the code**, with no behaviour change:
   - Application API: ORM classes, schemas, repositories, routes and file names (§P.2 *Related names*);
   - Agent System: `backend_client.py`, every service and MCP tool that passes `agent_run_id`/`step_id`, the `node_status` event, envelope version 2;
   - toolkit (Plan 1): `ToolContext.agent_run_id` → `run_id`, `attempt_id` added, `step_id` and `graph_version` removed; `RunAccepted` → `{node_id, change_id, status}`; the artifact refs `MaskSet.segmentation_batch_id` and `ResultSummary.run_id`;
   - frontend routes and fields (training UI, `ChatSidebar.tsx`); `CLAUDE.md` and `cubeai-docs/`.
3. **A static source scan** in the deterministic suite fails on any retired name outside migrations, old durable events and the problem catalog.

**Tests:** the migration up and down on a copy of the compose database; the existing contract suites pass with the new names; the source scan.

## Phase 6.1 — as built (2026-10-02)

**Status:** done. The migration and the mechanical code rename landed together on `New-Architecture`; the deterministic gate passes with the new names, and the source scan is part of it.

| Part | Commit | What landed |
| ---- | ------ | ----------- |
| Migration + backend rename | `0b77fcb2` | `o1f0a2c3d4e5_v1_o_renames.py`: 8 tables, 34 columns, 77 constraints, 20 indexes and 1 sequence renamed; 17 trigger functions re-created, 8 functions and 11 triggers renamed; the `batch_correlation_id` key and the `retained` / `render` values rewritten; a full `downgrade()`. The Application API (models, schemas, repositories, routes and file names), the Agent System (`backend_client.py`, services, envelope v2, `node_status`), the MCP tools and the toolkit fields |
| Frontend | `1d0958c2` | `/chat-messages`, `last_activity_at`, `created_at`, `control_state`, the `/runs` node status, `/segmentation-masks`; `utils/nodeStatusEvent.ts`, the one place that recognises the node-status event (`node_status`, and the legacy type for replays) |
| Tests and docs | `cd7bc7e8` | `tests/contracts/test_retired_vocabulary.py` (the source scan), `tests/integration/test_v1_o_renames_live.py` (6 live tests), the updated suites; `CLAUDE.md` and `cubeai-docs/database/README.md` |

**Verification:**

- **The migration, on a copy of the compose database** (37 sessions, 132 runs, 3,579 events, 348 stored files): n1 → o1 → n1 → o1 gives an identical catalog and identical per-table data hashes at each step; a fresh database built from an empty chain gives the same o1 catalog; the ORM-vs-database drift is the pre-existing one, renamed one to one; the renamed triggers still refuse what they refused before.
- **Suites:** deterministic gate 646 passed (643 on the clean baseline, plus the 3 scan tests); toolkit on Python 3.13 503 passed, 1 skipped; `legacy_autogen` + regression 130 passed (as on the baseline); live integration: the new o1 test 6 passed, the V1 migration suites 29 passed; frontend jest 18 passed.

**Differences from the plan text** (later plans should build on these):

- **One cut-over, three commits.** The step says "in one commit"; it landed as three consecutive commits, pushed together. Only `cd7bc7e8` passes the gate, because the tests are in it, so do not deploy or bisect to the first two.
- **Tool contracts kept their result keys.** The global rule (no success result shape changes) wins over the scan rule (old names only in migrations, old events and the catalog). Tool result keys such as `agent_run_id` and the `output_summary` tool argument stay, each line marked with the comment `tool result key, kept` or `tool argument name, kept`, which the scan accepts. `tools/database/result_shape.py` (`keep_model_id`) maps `inference_model_id` back to `model_id` in the results of the DB read tools. Plan 2 moves this file with the rest of `tools/database/`.
- **The scan** (`test_retired_vocabulary.py`) covers production source only: `application_api` (except `migrations/versions/`), `agent_system`, `mcp`, `scripts`, the storage modules, and the frontend `extensions`, `platform/app/src` and `modes`. Tests are not scanned, because they assert the history of old migrations. Its exact exceptions: `pipeline_agent_status` in `event_registry.py` and `nodeStatusEvent.ts` (old durable events are still replayed), and `output_summary` in `system_messages.py` (the tool argument the frozen prompts name).
- **Renames beyond the explicit map:** `inference_models.inference_model_id` (the table's own key, "`model_id` → `inference_model_id` wherever it appears"); trigger and function names and their error messages; the route `PATCH /sessions/{id}/control-state/{state}`; `/runs/session/{id}/by-target/{target_name}` with the response key `nodes_sharing_this_target`; the radiomics export column `radiomics_feature_id`; the MCP environment variables `RUN_ID` and `NODE_ID`; `domain_api.agent_run_id()` → `bound_run_id()`.
- **Kept on purpose:** PostgreSQL's own NOT NULL constraint names (not part of `NAMING_CONVENTION`); the `ARTIFACT_READ_*` environment variables (external configuration); the `sessions` CHECK values (narrowing them is Phase 6.2); the segmentation-mask repository function names (`get_segmentations…`).
- **The V1 live migration tests** (`test_v1_live_schema.py`, `test_v1_domain_live_schema.py`) stop at `n1f0a2c3d4e5` (`V1_HEAD`), because they seed data with the names those migrations were written in. `test_v1_o_renames_live.py` covers o1.
- **Line numbers** quoted by later plans (for example the test files in Phase 2.1) moved with the rename; search by name, not by line.

**Open follow-ups:**

- **Segmentation review wire key:** the frontend review still sends and receives the batch correlation id as `runId` / `run_id`; only the backend variable became `batch_correlation_id`. Decide before Plan 15, which replaces the review wait with a gate.
- Plan 0 (the 3.13 images) is still open; until it lands, the toolkit tests run only in a Python 3.13 container or venv, and the `cubeai1/agents-app` image needs `greenlet` installed to run the gate.
- Already failing before 6.1, not caused by it: the unmarked env.py test in `test_startup_migration_contract.py`, and `check_storage_boundary.ps1` on local runtime files under `Backend/storage`.

## Phase 6.2 — Cut-over 2: the session-graph schema (migration `p1f0a2c3d4e5_v1_p_session_graph`, `down_revision = o1f0a2c3d4e5`) ✅

Implements Appendix P §P.5–§P.8 with the 12 steps of §P.9 *Cut-over 2* (plus step 7a, D27):
1. the new tables `users`, `graph_changes`, `run_attempts`, `training_jobs`, `agent_checkpoints`, `agent_checkpoint_writes`;
2. the new columns:
   - `sessions` (`execution_mode` default `auto`, `privacy_mode`, `tokens_used`, `inbox_handled_through_event_id`, `user_id`) and `session_graphs.version`;
   - `graph_nodes` (`node_seq`, `target_kind`, `target_version`, `input_sources`, `expectations`, `accepted_by_change_id`, `removed_at`);
   - `node_runs` (`accepted_by_change_id`, `execution_policy`, `target_version`, `superseded_at`, `outputs`, `downstream_hold`, `hold_released_by_change_id`);
   - `run_attempts.suspension` and `run_attempts.resumed_at`, and the `suspended` state (D27);
   - `chat_messages.seq`;
3. every constraint, partial unique index and trigger of §P.8, including the shared fencing trigger (D24, also on the two checkpoint tables) and the two active-attempt indexes (per run in every phase; per session as a current-phase constraint; both count `suspended` as non-terminal);
4. the legacy backfills, with the exact semantics of §P.9:
   - approved plans → one applied change each;
   - drafts → a `pending` change with `approval_required:legacy_draft`, never applied;
   - never-started runs → `cancelled` as migration cleanup;
   - only provable attempts;
   - `outputs` only from an exact mapping;
   - chats deduplicated, or the migration stops;
5. `downgrade()` refuses in the cases listed in §P.9 step 12.

The code that stops writing progress statuses (`session_service.py:385-386`) ships in the same release, so the narrowed `control_state` CHECK never meets an old writer.

## Phase 6.3 — Models, schemas, repositories (the transactions) ✅

Follow the Application API layering (`api/routes` → `repositories` → `db/models` + `schemas`), one file per entity, named after the table. Every transaction runs inside `publication()` with the lock order session → graph → run → attempt → domain row.

| Repository function | Transaction (§P.8) | What it enforces |
|---|---|---|
| `create_session` | T0 | Owner, graph, chat, and the opening prompt as the first message |
| `propose_change` | T1 | New node ids unused; RFC 8785 digest; adds `PLAN_MODE` / `supersedes_completed`; withdraws or expires the old pending change; writes the Main's `tool_result` |
| `apply_change`, `decide_change` | T2 | CAS on `base_version`; sequence and `input_sources` checks; affected suffix; supersede; hold release; other pending changes `stale`; `tool_result` |
| `accept_input` | T3 | Message + `user_input` event |
| `start_node` | T4 | Readiness under lock; each pin is the current completed run of the selected live upstream node at resolution time; run + attempt 1, or a retry attempt by rules (a)–(f) (rule (f) reads `pending_tools` of the latest checkpoint for a `resumable` run); copies the `seed_from` checkpoint into the new run's thread when it is compatible (D27) |
| `mark_attempt_dispatched`, `mark_attempt_running` | T4 dispatch | `dispatched_at`; `preparing → running` allowed |
| `reconcile_attempt` | T5 | Backend outcomes; `record_lost`; `unknown` |
| `complete_attempt`, `fail_attempt` | T6 | `outputs` checked against the table allow-list; expectations → `downstream_hold`; `graph_idle`; usage |
| `request_cancel`, `confirm_stopped` | T7 | Timeout as a cancel request; the `phi_local` switch; a `suspended` attempt is confirmed stopped in the same transaction |
| `advance_inbox` | T8 | Monotonic cursor; turn token usage |
| `create_training_job`, `reconcile_training` | T9 | Receipt before the POST |
| `suspend_attempt`, `record_gate_decision`, `resume_attempt` | T10 | Suspend only with this attempt's interrupt checkpoint stored; the gate decision on the `user gate` path (with the domain gate columns in the same transaction); resume only when the session is `active`, the run current, and a gate has its decision |
| `put_checkpoint`, `put_checkpoint_writes`, `get_checkpoint`, `list_checkpoints`, `run_steps` | — (attempt path for writes) | Writes only by the run's current non-terminal attempt; reads by any attempt of the run; `pending_tools` and `spec_digest` kept in `metadata` |
| `purge_checkpoints` | — (harness path) | Deletes only the checkpoints of runs terminal for more than `AGENT_CHECKPOINT_RETENTION_DAYS` (default 14), and only those no live `seed_from` references |
| `session_activity` | — | The derived activity of §P.4 |

One helper, `fenced_transaction(path)`, opens every transaction on a fenced table and sets the D24 path with `SET LOCAL`. The static scan of §P.5 requires it.

## Phase 6.4 — Routes (thin; registered in `application_api/main.py`) ✅

OpenResty strips the `/api` prefix, as today. Every session-scoped route filters by `current_user` (the configured default user until authentication exists). Write routes on the execution path read `X-Attempt-Id` (or `X-Run-Id` for an internal job) and pass it to `fenced_transaction`.

| Method | Path | Transaction |
|---|---|---|
| POST | `/sessions` | T0 |
| PATCH | `/sessions/{id}/settings` | `execution_mode`, `privacy_mode` (a switch to `phi_local` also runs T7) |
| GET | `/session-graphs/session/{id}` | Version, live nodes with their current runs, the pending change, the derived activity |
| POST | `/session-graphs/session/{id}/changes` | T1 (reasons present) or T2 (no reasons); 409 `STALE_BASE` or `IDEMPOTENCY_CONFLICT` |
| POST | `/graph-changes/{change_id}/decision` | T2 or reject; body `{decision, content_digest, comment}`; user only |
| GET | `/graph-changes/session/{id}` | History and the pending change |
| POST | `/chats/session/{id}/input` | T3 (called by the Agent System) |
| POST | `/runs/start` | T4 (first start or retry) |
| POST | `/runs/{run_id}/attempts/{attempt_id}/dispatched`, `/running` | T4 dispatch steps |
| POST | `/runs/{run_id}/attempts/{attempt_id}/complete`, `/fail` | T6 |
| POST | `/runs/{run_id}/attempts/{attempt_id}/cancel`, `/stopped` | T7 |
| POST | `/runs/{run_id}/attempts/{attempt_id}/reconcile` | T5 |
| POST | `/runs/{run_id}/attempts/{attempt_id}/suspend`, `/resume` | T10 suspend / resume |
| POST | `/runs/{run_id}/attempts/{attempt_id}/gate-decision` | T10 gate decision for a gate with no domain row (`tool_approval`); body `{interrupt_id, decision: approve\|reject, comment}`; user only |
| PUT | `/runs/{run_id}/checkpoints` | Store one checkpoint (attempt path); body `{checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint (base64), metadata}` |
| POST | `/runs/{run_id}/checkpoint-writes` | Store pending writes of one checkpoint (attempt path) |
| GET | `/runs/{run_id}/checkpoints/latest`, `/runs/{run_id}/checkpoints/{checkpoint_id}`, `/runs/{run_id}/checkpoints` | One checkpoint with its writes (`?ns=`); a filtered list (`?ns=&before=&limit=`) |
| GET | `/runs/{run_id}/steps` | The step timeline from checkpoint metadata (no blobs): step, node, tool calls, timestamps |
| POST | `/agent-checkpoints/purge` | Retention purge (harness path), called by the Agent System sweep |
| GET | `/runs/{run_id}`, `/runs/node/{node_id}` | A run with its attempts; a node's run history |
| POST | `/sessions/{id}/inbox-cursor` | T8 |
| POST / GET | `/training-jobs`, `/training-jobs/{job_id}` | T9 |

The user-gate routes that exist today (`/populations/approve-cohort` and the segmentation review) set the D24 `user gate` path, and run T10's gate-decision step in the same transaction: they record the decision on the suspended attempt and publish `gate_resolved`. The segmentation review long-poll (`/segmentation-review/wait`) gets no new agent caller (Phase 15.4). The old `/plans/…/approve` returns `410 Gone`.

## Phase 6.5 — Agent System client ✅

1. **`clients/backend_client.py`:** one method per route above, plus `add_chat_message` / `list_chat_messages` for the Context Manager. Each execution write sends the D24 header taken from `current_context()`.
2. **`execution/graph_store.py`:** a typed facade (`GraphStore`) over those methods, plus `InMemoryGraphStore` for the tests of Plans 8–13. The in-memory store enforces the same invariants as the database (CAS, one pending change, one current run per node, one active attempt per session, fencing, `suspended` transitions, checkpoint fencing), so the fakes are never looser than the real thing. It also backs the in-memory checkpoint store that `HttpCheckpointSaver` uses in unit tests (Phase 9.5).

**Tests:**
- **Contract:**
  - `tests/contracts/test_v1_schema_contract.py` covers every table, CHECK, FK and partial index of §P.8;
  - the fencing trigger is attached to every table with `created_by_run_id`, plus `segmentation_pairs`, `stored_files`, `node_runs`, `run_attempts`, `agent_checkpoints` and `agent_checkpoint_writes` (checked in `pg_trigger`);
  - every producer in the allowlists exists in the catalog (Plans 2 and 7);
  - every repository write to a fenced table goes through `fenced_transaction`.
- **Unit:** route validation with fake repositories; the derived activity; `InMemoryGraphStore` against the same scenario table as the integration tests.
- **Integration** (`-m "integration and docker"`, against the compose Postgres):
  - both migrations run up and down;
  - two concurrent T2 on one base: one wins, the other gets `STALE_BASE`; an Auto change marks a pending one `stale`; a decision with a wrong digest is refused;
  - T4 refuses a pin that is not the current completed run of the selected live upstream node, and a consumer started on run 1 still reads run 1 after the upstream node is rerun;
  - a second active attempt in one session is refused;
  - a write with a stale `attempt_id` is refused, a write racing T6 waits on `FOR SHARE`, and a write with no declared path is refused on every fenced table;
  - a selection change after completion is refused; the user-gate path works only while the run is running and current;
  - the legacy backfills: a draft becomes a pending change and is never applied; a pending run becomes `cancelled` with `source: migration`; `outputs` only from an exact mapping;
  - **D27:** a checkpoint write from a stale or terminal attempt is refused; attempt 2 reads attempt 1's checkpoints; T5 leaves a `suspended` attempt unchanged; T10 refuses a suspend without the interrupt checkpoint, and a resume while paused or before the gate decision; a gate decision on a superseded run is refused; T7 on a `suspended` attempt is confirmed at once; a `seed_from` checkpoint is copied only when `spec_digest` and `privacy_mode` match, otherwise the run starts fresh with `seed_from.applied = false`; the purge never deletes a non-terminal run's checkpoints.

**Done when:** both migrations apply and reverse, and every test above passes.

## Phases 6.2–6.5 — as built (2026-10-05)

**Application API (6.3, 6.4):**
- **Models** mirror migration p1 exactly; a live test compares the ORM with the migrated schema (alembic `compare_metadata`) for every table and index p1 created. New: `users`, `graph_changes`, `run_attempts`, `agent_checkpoints`, `agent_checkpoint_writes`, `training_jobs`. Relationships that would drag history (`Session.chats/events`, `SessionGraph.nodes`, `GraphNode.runs`, `Chat.messages`) are `lazy="raise"`.
- **Pure rules, no database:** `core/graph_content.py` (`ChangeContent`, `plan_apply()`: inserts, edits, removals, the affected suffix, supersession, `PLAN_MODE` / `supersedes_completed`) and `core/execution_rules.py` (outputs allow-list, expectations, retry rules (b)–(f), derived activity). T1 and T2 share `plan_apply()`, so a reason derived at proposal is re-derived identically at decision.
- **D24:** `services/fencing.py`. `fenced_transaction(db, path)` declares the path on every transaction the session begins inside the block, and flushes pending writes before it hands the transaction back to the outer path. `fenced_db` is the request session of a write route on the execution path (from `X-Attempt-Id` / `X-Run-Id`); `@on_path(USER)` wraps the in-node gate repositories (cohort approval, segmentation review), which also record the T10 gate decision in the same transaction. A database refusal is a 409 `{code: REFUSED_BY_DATABASE, message, sqlstate}`; harness refusals carry stable codes (`STALE_BASE`, `IDEMPOTENCY_CONFLICT`, `NOT_READY`, `ATTEMPT_ACTIVE`, `STALE_DEFINITION`, `BAD_PIN`, `MAX_ATTEMPTS`, `NEEDS_DECISION`, `CANCELLED`, `NOT_RESUMABLE`, `NO_OPEN_GATE`, `INVALID_CHANGE` (422, with per-node errors), …).
- **The static scan** (`tests/contracts/test_v1_harness_contract.py`) works on routes: every write route that reaches a repository writing a fenced table either takes `Depends(fenced_db)` or calls repository functions that open `fenced_transaction` / `@on_path` / the shared lifecycle `transition()`. A mutation check (dropping `fenced_db` from one route) fails it.
- **Repositories:** `sessions` (T0, settings + the `phi_local` T7 switch, T8, `session_activity`), `chats` (T3; one chat per session), `graph_changes` (T1/T2), `node_runs` (T4, reads, scratchpad), `run_attempts` (T4 dispatch, T5, T6, T7, T10), `agent_checkpoints`, `training_jobs` + `services/training.py` (T9; `/workflow/{task}/train` and its job routes delegate to it). Every lifecycle transaction publishes `node_status` and `session_state`; the new durable event types are registered in `event_registry.py`.
- **Content shapes** (also the `graph_nodes` columns): `input_sources = {v, ports: {port: {node_id, port}}}`, `expect = {v, rules: [{metric, op, value, port?}]}`; an optional `seed_from {run_id, checkpoint_id, checkpoint_ns?}` sits on the `NodeDef` of a node the change reruns (agent nodes only), where T4 and the purge's `$.**` path find it. Plan 8 builds these shapes.
- **Routes:** as in the 6.4 table, plus `GET /sessions/{id}/activity`. `/session-graphs/.../approve` and `PUT .../nodes` answer `410 Gone`; the graph-node write routes are gone (nodes change only through T2). `node_status` is schema v2: every live node, `PENDING` before its first run, `CANCELLED`, `attempt_state`, `waiting_for_user`, `holds_downstream`.
- **Owner:** `DEFAULT_USERNAME` (default `default`) is the configured user until authentication exists. Pre-cut-over sessions belong to `legacy`; set `DEFAULT_USERNAME=legacy` to keep working in them.
- **Ownership on every session-scoped route (2026-10-06).** A route whose path names a session or a row of one depends on `current_user` through an `owned_*` dependency in `api/dependencies.py` (`owned_session`, `owned_run`, `owned_chat`, `owned_message`, and one per domain row: cohort, feature set, model result, segmentation batch, literature review, radiomics row, report, file, event; the four rows a run produces reach their session through `created_by_run_id`). Another user's session answers 404, never 403. Creates that name their session in the body call `require_owner` first. The cross-session listings (`/session-events/`, `/session-events/filter`, `/radiomics/`, `/radiomics/search/`, `/radiomics/by_label/…`) return only the caller's rows (`owner=` in the repository; `users.owned_session_ids`). `GET /files/{id}/content` checks ownership on the binary pool, so a download holds no main-pool connection. `tests/contracts/test_route_ownership_contract.py` scans the dependency tree of every registered route, so a new route without the check fails the deterministic suite; only the two `410 Gone` routes are exempt. A WebSocket opened for a session the caller does not own now gets `replay_failed` instead of an empty replay.

**Agent System (6.5):**
- `clients/harness_client.py` (`HarnessClient`): one method per route, refusals as `ApplicationApiError(code)`, 5xx bodies never surfaced. An attempt's own writes (running, complete, fail, checkpoints) send `X-Attempt-Id` from their attempt id, not from `current_context()`: the toolkit is Python 3.13-only (Plan 0), and the id is already a parameter. The legacy `BackendClient` stays for the frozen AutoGen code until Plan 17.
- `execution/graph_store.py` (`GraphStore` protocol, typed records, `HttpGraphStore`) and `execution/in_memory_graph_store.py` (`InMemoryGraphStore`). One scenario table (`tests/support/graph_store_scenarios.py`, 10 scenarios) runs on both: in the unit suite on the fake, live on `HttpGraphStore` through the real app and PostgreSQL.

**How to test:**
- Deterministic gate: `Backend/tests/run_tests.ps1 -Suite deterministic` (or pytest directly, see CLAUDE.md). `greenlet` is now in `requirements/test.txt`.
- Live database suites (throwaway PostgreSQL 18, never the compose one): `powershell -NoProfile -ExecutionPolicy Bypass -File Backend/tests/run_live_db_tests.ps1` — Cut-over 1 and 2 up/down, T0–T10 through HTTP, D24 fencing, `pg_trigger` coverage, ORM == schema, the GraphStore scenarios, ownership across users, a selection after completion, the user gate path, and a write racing T6 (56 tests).

**Open follow-ups:**
- **D26 in practice:** the frozen AutoGen path still calls the retired plan routes (`approve_plan`, run start/complete/fail), so the legacy pipeline cannot run against this schema. Plans 8–13 replace it; Plan 17 deletes it.
- The Agent System's `/input` still queues in memory; moving it onto T3 (`HarnessClient.accept_input`) belongs with the Main loop (Plans 10–11).
- ✅ Closed 2026-10-06: ownership on the domain routes (see **Ownership** above).
- ✅ Closed: the pre-existing drift the ORM/schema comparison found outside p1. Migration `q1f0a2c3d4e5` made `sessions.created_at` timezone-aware (`test_every_session_timestamp_is_timezone_aware`), and the older indexes (`ix_sessions_updated_at`, `ix_chat_messages_chat_id_created_at`) are declared in the models.
- ✅ Closed 2026-10-06: the three integration checks of 6.5 that had no test. `test_a_selection_cannot_change_after_its_run_completed` (every write path refuses to freeze what a completed run produced), `test_the_user_gate_path_works_only_on_a_run_suspended_on_its_gate` (refused while running but not suspended, accepted while suspended on the gate and only for the gate columns, refused once cancelled; superseded runs go through the same `fencing_require_current_run` check), and `test_a_write_racing_completion_holds_it_off_until_it_commits` (T6 waits on the write's `FOR SHARE`, then the attempt's later writes are refused).
- The Plan 7 job tools (`run_modeling`, `train_*`, `run_*_inference`, `refit_model`) are on the `model_results` allow-list before they exist; the catalog contract lists them as pending and fails once one lands, so the list is kept honest.

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

   | Old name                           | New name                   |
   | ---------------------------------- | -------------------------- |
   | `pycaret_train_classifier`         | `train_classifier`         |
   | `pycaret_train_regressor`          | `train_regressor`          |
   | `pycaret_run_inference`            | `run_classifier_inference` |
   | `pycaret_run_regression_inference` | `run_regression_inference` |
   | `pycaret_refit_model`              | `refit_model`              |

   The old names appear nowhere in new code. They survive only in old durable events and in the frozen AutoGen files, which Plan 17 deletes.

2. **Base manifest for all 7 heavy tools:**
   - `execution="job"`, `side_effects="compute"`, `cost="heavy"`, `resources=["cpu_heavy"]`;
   - `trust="first_party"`, `isolation="process"`.

| Tool                                                                  | `visibility`       | Ports                                                                  |
| --------------------------------------------------------------------- | ------------------ | ---------------------------------------------------------------------- |
| `train_classifier`, `train_regressor`                                 | {`graph`}          | `features: FeatureTable` → `modeling: ModelingOutput`                  |
| `run_classifier_inference`, `run_regression_inference`, `refit_model` | {`graph`}          | `modeling: ModelingOutput` (+ `features`) → `modeling: ModelingOutput` |
| `radiomics_extract_features`                                          | {`agent`, `graph`} | `cohort: Cohort`, `masks: MaskSet` → `features: FeatureTable`          |
| `radiomics_analyze_importance`                                        | {`agent`, `graph`} | `features: FeatureTable` → `importance: FeatureImportance`             |

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
   - writes `model_results` with producer `run_modeling`, through the D24 attempt path;
   - returns a result from which the `result` port extracts `ModelingOutput`.
4. **UI and progress:**
   - `on_result` emits the `modeling_output` view event, copied from `pipeline_monitor.py:182-252`, so `ModelingView` keeps working;
   - progress goes through the context sink.

## Phase 7.4 — Correctness for jobs

Fix H1–H18 (Appendix C). Two of them change code outside the tools:

- H12 changes `application_api/repositories/radiomics.py`, so that reused rows are linked to the current run, unless `execution_policy.fresh` is set; then nothing produced by another run is reused (Appendix P).
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

**Goal:** deterministic graph code for a sequential live graph: a graph model, a Validator that returns only technical errors, the strict input resolution (D23), the affected set, and atomic, versioned changes through T1/T2 (system §9, §26, §27; Appendix P).

## Phase 8.1 — Graph model (`planning/graph_model.py`, pure Pydantic)

1. **The model:**
   - `NodeDef(node_id, kind, ref, ref_version, title, params, input_sources, expect)`. `node_id` is a UUID minted by whoever proposes the node (D7); there are no local refs. `title` is part of the definition (`graph_nodes.title`, §P.6), so it is in the change content and renaming a node is an edit;
   - `InputSource(node_id, port)`; `Expectation(metric, op, value)`, where `op` is one of `== != < <= > >=`;
   - `GraphState(version, nodes)`: the live sequence in `position` order, each node with a summary of its current run.
2. **`GraphDiff(base_version, ops, on_running = "reject" | "restart")`.** The ops are:
   - `AddNode(at?)`, `RemoveNode`, `MoveNode`, `UpdateParams`;
   - `SetInputSources`, `SetExpectations`, `ReleaseHold`, `RerunNode(seed_from?)`.

   There is no `Rebind` or `AddAfter`: the order is the sequence (D5).

   `seed_from = {run_id, checkpoint_id}` (agent nodes only, D27) asks the new run to start from that checkpoint of an earlier run of the **same** node instead of from scratch, for example the step before a bad tool call. It is still a new run (I8), so it supersedes like any rerun and carries `supersedes_completed` when it should.
3. **`ChangeContent`:** `apply_ops(state, diff)` produces the full target sequence with `v`, the node definitions, `rerun_node_ids` (each with its optional `seed_from`), `release_hold_node_ids`, `on_running` and an optional `origin`. This is exactly `graph_changes.content` (Appendix P); the Application API computes its digest.
4. **`GraphProposal(proposal_id, diff, rationale, errors)`** stays in memory (D19).

## Phase 8.2 — Validator (`planning/validator.py`)

`validate(state, content, catalog) -> list[ValidationError(code, node, detail)]`, over the **whole** target sequence. It never suggests a strategy (system §9).

| Code | When |
|---|---|
| `UNKNOWN_REF` | `ref` is not in the catalog |
| `NOT_GRAPH_VISIBLE` | The tool or agent cannot be a node (not `graph` / not `execution`) |
| `INVALID_PARAMS` | The params fail `input_model` |
| `MISSING_INPUT` | A required input port has no candidate (D23) |
| `AMBIGUOUS_INPUT` | An input port has more than one candidate and no `input_sources` entry; the error lists the candidates |
| `BAD_INPUT_SOURCE` | An `input_sources` entry names a node that is not live and earlier, or a port that is not a candidate |
| `TYPE_MISMATCH` | An `input_sources` entry names an incompatible output type (e.g. "expected MaskSet, received ImageSet") |
| `UNKNOWN_PORT` | The port does not exist on the source or the target node |
| `DUPLICATE_NODE` | A new `node_id` is already used, or appears twice |
| `BAD_EXPECTATION` | The metric is not in the output type's summary model |
| `UNKNOWN_RESOURCE` | A declared resource pool is not in `config/resources.yaml` |
| `RUNNING_NODE_AFFECTED` | The change affects a running node and `on_running="reject"` |
| `BAD_SEED` | `seed_from` on a tool node or a non-`resumable` agent, or its `run_id` is not a run of this node. Whether the checkpoint still exists and is compatible is checked by T4, which then starts fresh |
| `STALE_BASE` | `base_version` is not current (returned by T2) |

There is no `CYCLE_DETECTED`, `DANGLING_REF` or `REMOVE_WITH_DEPENDENTS`. Removing a node simply runs input resolution again, so a dependant that loses its only candidate gets `MISSING_INPUT`.

## Phase 8.3 — Input resolution and affected set (`planning/inputs.py`, `execution/affected.py`)

1. **`resolve_inputs(sequence, node, catalog) -> {port: InputSource} | errors`** is the D23 rule. Candidates are the compatible output ports of earlier live nodes:
   - exactly one → bound;
   - none → `MISSING_INPUT` (required) or empty (optional);
   - more than one → the `input_sources` entry, or `AMBIGUOUS_INPUT`.

   The Validator and the Node Runner (before T4) use the same function, so a pin always matches what was validated.
2. **`affected(old, new)`:** the changed, moved, rerun or removed nodes, plus every live node after the first of them. This is the sequential suffix, a current-phase constraint (Appendix P §P.4). It mirrors T2 step 5 exactly, and a contract test runs both on the same cases.
3. **No fingerprints now** (Appendix P §P.10).

## Phase 8.4 — Diff Engine (`execution/diff_engine.py`)

1. **`plan_change(state, diff) -> ChangeOutcome`** with `content`, `affected`, `runs_to_supersede` and `errors`. It applies the §27 rules to a sequence:

   | Node state | Unaffected | Affected |
   |---|---|---|
   | not started | stays | its definition is updated; there is nothing to cancel |
   | running | continues | `on_running="restart"`: superseded, its attempt gets a cancel request, and the next start (after the stop is confirmed) creates a new run. Otherwise: `RUNNING_NODE_AFFECTED` |
   | completed / failed | its current run stays; that is the reuse | superseded, never deleted; the next start creates a new run; a completed run adds `supersedes_completed` |

2. **`apply(diff, author, turn_id?, call_id?)`:** runs the Validator, then `GraphStore.submit_change`: T2 when there are no approval reasons, T1 otherwise. For the Main the idempotency key is `sha256(turn_id‖call_id)`. On success it wakes the Executor (`graph_changed`). The durable events come from the Application API.
3. **`decide(change_id, decision, content_digest)`** for user decisions (Plan 11).

**Tests (`tests/unit/planning/`, `tests/unit/execution/test_diff_engine.py`):**
- every Validator code, including `AMBIGUOUS_INPUT` for two compatible producers and for one node with two compatible ports, and an insert that makes a later node ambiguous;
- the §27 matrix (3 states × affected/unaffected × `on_running`);
- the A→New→B example (B is affected); `affected()` equals the T2 rule;
- `RerunNode(seed_from)`: accepted for a run of the same resumable agent node, `BAD_SEED` otherwise; it supersedes like any rerun;
- a stale base; new node ids unused; superseded outputs are never deleted.

**Done when:** all the Validator and Diff Engine tests pass with `InMemoryGraphStore`.

---

# Plan 9 — LangGraph Engine, Checkpoints and Model Gateway

**Goal:** real LLM execution for every agent through LangGraph, with model selection, fallback and locality handled by one gateway. Agent nodes are LangGraph-native (decision 2026-10-02): they are checkpointed through the Application API (Appendix P D27), they suspend on gates and pause instead of holding a process, their tool calls survive a resume as tasks, they fan out per-item work inside the node, and they compact their own context. The Main, the Composer and the Recovery Agent run on the same engine **without** a checkpointer.

## Phase 9.1 — Dependencies

- Add `langgraph` (it brings `langgraph-checkpoint`, which holds `BaseCheckpointSaver` and the serializer), `langchain-core` and `langchain-openai`. Pin the exact versions when implementing: the checkpoint format depends on the `langgraph` version (D27). They go into `Backend/agent_system/requirements.txt` (UTF-16 LE, CRLF) and `deploy/Dockerfile.agents`.
- **Not added:** `langgraph-checkpoint-postgres`, `langgraph-checkpoint-sqlite`, `langgraph-sdk` / LangGraph Server, and any `BaseStore` implementation. The only checkpointer is `HttpCheckpointSaver` (Phase 9.5); a search check in Plan 17 enforces this.
- **Upgrading `langgraph`** while runs are `suspended` or `interrupted` is a release step: either let them finish, or accept that their resume fails with `checkpoint_incompatible` (Phase 9.4) and the node is rerun.

## Phase 9.2 — Model Gateway (`harness/model_gateway.py`, `config/models.yaml`)

1. **Profiles:** `main`, `composer`, `recovery`, `agent_default`, `extraction` (cohort labels) and `summarizer`. Each profile has a primary model and a list of fallbacks.
2. **A model entry:** `provider` (`openai|azure_openai|ollama`), `name`, `base_url_env`, `api_key_env`, `locality` (`local|external`), `capabilities` (`tools`, `json`) and `max_tokens`.
3. **`get_chat_model(profile, ctx) -> BaseChatModel`:**
   - builds `ChatOpenAI` (for OpenAI, and for Ollama through its OpenAI-compatible URL) or `AzureChatOpenAI`;
   - adds `.with_fallbacks(...)`;
   - retries with the delays from `LLM_RETRY_DELAYS_SECONDS`, reusing `client_config._retry_delays`.
4. **User settings:** the existing `/client-config` settings (`ClientSettingsPanel`) still override the default entry.
5. **`locality_filter(ctx)` hook:** empty now; Plan 13 fills it for PHI-Local.
6. **Usage:** a usage callback counts tokens in memory. They are written once per attempt (`run_attempts.usage`, T5/T6/T7) and once per Main turn (T8), and added to `sessions.tokens_used` there. The budgets in Plan 11 use the persisted total plus the in-memory counter.

## Phase 9.3 — Toolkit hooks for the engine (Plan 1 follow-up)

These extend Plan 1 contracts. `toolkit/` still imports nothing from LangGraph (`test_boundary.py` unchanged): the engine plugs its behaviour in.

1. **`AgentStatus.SUSPENDED`** and `AgentResult.suspension = Suspension(kind: gate|pause, gate?, interrupt_id, payload, checkpoint_id)`. A suspended result has a `suspension` and no `error_code`.
2. **`AgentEngine.run(spec, task, inputs, ctx, cancel=None, resume: Resume | None = None)`.** `Resume(interrupt_id, value)` continues a suspended run; `Resume.continue_()` continues an interrupted one from its last checkpoint. `FakeAgentEngine` records `resume` and can script a suspension.
3. **`ToolContext.user_gate(gate, payload) -> GateDecision`:** suspends the node until the user decides. `payload` holds refs and summaries only. Called outside an agent node (a Main direct call, an inline tool node) it fails with `invalid: REQUIRES_NODE`, because only a node can wait.
4. **`ToolContext.durable(key, fn)` and `ToolContext.durable_map(key, items, fn, max_concurrency)`:** run a side effect once per run, so a resume reads its stored result instead of running it again. The result must be small and JSON (refs, ids, summaries). With no engine they simply await `fn`.
5. **`Dispatcher(passthrough=…)`:** exception types that dispatch re-raises instead of turning into `error_result`. The engine registers LangGraph's interrupt exceptions (`GraphInterrupt` is an `Exception`, so without this dispatch would swallow every gate).
6. These hooks live on the live part of `ToolContext` (an `engine_hooks` field next to `event_sink`), so they are left out of `to_payload()` like the other live fields.

## Phase 9.4 — LangGraph engine (`toolkit/engines/langgraph_engine.py`)

`LangGraphAgentEngine` implements `AgentEngine`. It builds a custom `StateGraph`, not the prebuilt agent, so that every tool call goes through our dispatch.

1. **State:** `{messages (add_messages reducer), iterations, completion_retries, output}`.
2. **Nodes:**
   - **`gate`:** when a pause is requested for the session, calls `interrupt({"kind": "pause"})`, so the attempt is suspended at a step boundary (T10). While the node runs, it also moves `node_message` texts from the in-memory mailbox into `messages` as human messages, so they are checkpointed;
   - **`compact`:** when `messages` exceed the agent's `max_context_tokens`, summarizes the older messages with the `summarizer` profile and removes them with `RemoveMessage`. It keeps the system prompt, the "Node inputs" message and the latest turns, and never separates a tool call from its result. The summarizer must pass the same `locality_filter` as the agent's own model (Plan 13). The compaction is part of the checkpoint;
   - **`model`:** the gateway model bound to the tool schemas;
   - **`tools`:** runs each tool call as a LangGraph **`@task`** that calls `dispatch.invoke`, in parallel only when every called tool is `read_only`. A finished call's result is stored with the checkpoint, so a resume never repeats it; only a call that was still running is run again (Appendix P T4 rule (f));
   - **`check`:** runs `completion_check` / parses `output_contract`. On failure, it adds a correction message while `completion_retries < max_completion_retries`.
3. **Edges:** `gate → compact → model`; `model →` `tools` when there are tool calls, otherwise `check`; `tools → gate`; `check → END` or `gate`.
4. **Checkpointer:** a `resumable` spec with a run in its context is compiled with `HttpCheckpointSaver` (Phase 9.5), config `thread_id = run_id`, `checkpoint_ns = ""`, and the run metadata. Control agents and stand-alone internal agents are compiled without one. Use the `async` durability mode (each checkpoint is stored while the next step runs, and LangGraph waits for it before an interrupt or exit); verify the mode name against the pinned version.
5. **Gates:** `ctx.user_gate()` calls `interrupt({"kind": "gate", "gate", "payload"})`. On resume, LangGraph runs the interrupted task again from its start and `interrupt()` returns the decision; therefore every side effect before a gate goes through `ctx.durable()` (Definition of Ready — every tool, item 13).
6. **Outcome of `run()`:**
   - the graph stops on an interrupt → `AgentResult(status=SUSPENDED, suspension=…)`; the Node Runner runs T10 suspend (Plan 10);
   - `resume=Resume(id, value)` → `ainvoke(Command(resume=value), config)`; `resume=Resume.continue_()` → `ainvoke(None, config)`;
   - a checkpoint whose `engine_version` or `spec_digest` differs from the running engine and spec → `FAILED` with `error_code="checkpoint_incompatible"`. That failure goes to the inbox, and the fix is a rerun (a new run starts a new thread).
7. **Limits:** `recursion_limit` is derived from `max_tool_iterations`. Reaching the cap ends with `error_code="max_iterations"`.
8. **Tool schemas:** `StructuredTool.from_function(coroutine=..., args_schema=spec.input_model)`. External tools use their raw JSON schema.
9. **Structured output** (for the composer and recovery agents): a final `submit_output` tool whose schema is the `output_contract`.
10. **Cancel:** cancelling the run task raises `CancelledError`, and the cancel token reaches `dispatch.invoke`, so a running job is cancelled (Plan 5). A cancelled run is never resumed automatically.
11. **Streaming:** `astream(stream_mode=["messages", "custom", "updates"], subgraphs=True)`. `messages` → ephemeral `stream_chunk` events (the Main Agent); `custom` → tools report progress through `get_stream_writer()` as ephemeral `tool_progress`; `updates` → the in-memory heartbeat the Watchdog reads (Phase 10.5). Events from a subgraph carry its namespace as `subagent`.
12. **The Main, the Composer and the Recovery Agent:** no checkpointer, no `interrupt()`. Each `run()` is one Main turn or one agent-as-tool call, and the Main's history comes from the Context Manager (Plan 12, D21). Graph-change approvals stay harness approvals (D7), never interrupts.

## Phase 9.5 — Checkpointer (`execution/checkpointer.py`)

1. **`HttpCheckpointSaver(BaseCheckpointSaver)`** stores every checkpoint through the Application API (Phase 6.4) and never touches PostgreSQL (D1). It implements `aget_tuple`, `alist`, `aput` and `aput_writes`; the sync methods raise, because the Agent System is async only; `adelete_thread` raises, because deletion is the retention purge only.
2. **Mapping:** `thread_id` is the `run_id`; `checkpoint_ns` and `checkpoint_id` are stored as given. Values are serialized with LangGraph's serializer and sent as `{type, base64}`.
3. **Metadata it adds on `aput`:** `engine_version` (the `langgraph` version), `spec_digest` (SHA-256 of the agent's prompt, tool names, `version` and `workflow`), `step`, and `pending_tools` (`[{name, idempotent}]` from the tool calls of the last model message). T4 rule (f) reads `pending_tools` (Appendix P §P.8).
4. **Fencing:** every write sends `X-Attempt-Id` from `current_context()`. A fencing refusal raises `StaleAttempt`, which stops the engine: the attempt was superseded or cancelled.
5. **Size:** a checkpoint above `AGENT_CHECKPOINT_MAX_BYTES` (default 2 MiB) is refused locally with `checkpoint_too_large`. Messages hold tool results as refs and summaries (Definition of Ready — every tool, item 5), which keeps checkpoints small.
6. **Retention sweep:** the Agent System calls `POST /agent-checkpoints/purge` every `AGENT_CHECKPOINT_PURGE_S` (wired in Plan 17).
7. **Tests** run one conformance suite against `HttpCheckpointSaver` (respx, fake Application API) and the in-memory store of `InMemoryGraphStore`: put/get/list, writes, namespaces, metadata, fencing refusal, size refusal.

## Phase 9.6 — Workflow agents and fan-out (`toolkit/engines/workflow.py`)

1. **Workflow agents:** for an agent with `workflow` (Plan 3), the engine compiles `hooks.build_graph(ctx)` with the checkpointer instead of the default loop. Each step that is an internal agent runs as a subgraph (that agent's default graph) in its own checkpoint namespace, so a resume continues inside the step. The parent's `completion_check`, `output_contract` and ports are unchanged.
2. **The literature review graph:** `pico_analyst → query_builder → search_executor → relevance_ranker → context_compiler`.
   - `search_executor` fans out with `Send`, one branch per search source;
   - `relevance_ranker` fans out with `Send`, one branch per paper (`score_paper_relevance`);
   - `context_compiler` is a deferred join over the branch results, and calls `save_literature_output`.
3. **Fan-out rules (both `Send` and `ctx.durable_map`):**
   - finished branches are stored with the checkpoint, so a resume runs only the unfinished items;
   - concurrency is the run config's `max_concurrency`, taken from the `llm` pool size in `config/resources.yaml`, so one node cannot flood Ollama;
   - branch token usage is added to the attempt's usage;
   - this is parallelism **inside** one node, like the segmentation fan-out (D10). The Executor still runs one node at a time (D5).

## Phase 9.7 — Engine consumers inside tools

1. **The segmentation module:**
   - `module.py:96-133` (which builds AutoGen agents from the MCP workbench) and `:1375-1376` now call the engine: `AgentEngine.run("clinical_target_intent_agent" | "mask_resolution_agent", ...)`;
   - the calls go through the `run_agent` core, so depth, events and cancel work. A `run_agent` call is one task of the parent node: once it finishes, a resume does not run the child again;
   - `planner.py` keeps its prompts and parsing, and loses its `AssistantAgent` factories;
   - **the review gate:** the pair work runs inside `ctx.durable("segmentation_pairs", …)`, then `await ctx.user_gate("segmentation_review", {segmentation_batch_id, n_pairs, review_request_hash})` replaces `_wait_for_review` and the long-poll. After the resume, the tool reads the decision from the domain rows (D25); the gate value only says that the user decided.
2. **The population tools:** cohort creation runs inside `ctx.durable("create_cohort", …)`, then `await ctx.user_gate("cohort_approval", {cohort_id, size})`. The approval route freezes the cohort, as today.
3. **`cohort_labels.py`:** its LLM calls use the gateway `extraction` profile, one call per patient through `ctx.durable_map("cohort_labels", patients, …)`, so a resume labels only the patients that were not done.

**Tests (`tests/unit/engines/`)** use `langchain_core` fake chat models (`GenericFakeChatModel` / `FakeMessagesListChatModel` with scripted tool calls), with no network, and the in-memory checkpoint store:

- the tool-call path through dispatch (events, error envelope); `GraphInterrupt` passes through dispatch;
- the iteration cap; completion retries; `output_contract` parsing;
- **resume:** stop the engine after tool call N; a new `run(resume=Resume.continue_())` finishes without calling tools 1..N again, and calls N again only if it had not finished;
- **gates:** a tool's `user_gate` returns `SUSPENDED`; `Resume(value)` continues; the `ctx.durable` step before the gate runs once in total; `user_gate` from a Main direct call is refused;
- **pause:** a pause request suspends at the next `gate` node; resume continues;
- **compaction:** above `max_context_tokens` the older messages are replaced by one summary, tool calls stay paired with their results, and the summarizer respects the locality filter;
- **fan-out:** the literature graph with 3 sources and 5 papers; a crash after 3 papers resumes only 2; `max_concurrency` holds; `durable_map` in `cohort_labels` behaves the same;
- `checkpoint_incompatible` on a changed `spec_digest`; `StaleAttempt` stops the engine;
- the Main, Composer and Recovery run with no checkpointer;
- cancel stops a running tool; fallback on a model error; usage recorded, including fan-out branches;
- the segmentation module calls the internal agents through the engine.

**Done when:** every `AgentSpec` can run on `LangGraphAgentEngine` with a fake model; every `resumable` execution agent passes a resume test; the gate, pause, compaction and fan-out tests pass; and `FakeAgentEngine` still passes the Plan 1 tests.

---

# Plan 10 — Executor, Node Runner, Watchdog

**Goal:** the only component that runs work (system §11-§14, §24), over the persistence of Appendix P. It starts nodes with T4, completes them with T6, cancels with T7, reconciles with T5, and suspends and resumes agent nodes with T10. It absorbs `RunHarness` and `DirectToolExecutor` from tools_plan.

## Phase 10.1 — Node Runner (`execution/node_runner.py`)

`run(node)`:
1. `catalog.resolve(node.ref)`. Resolve the inputs with `planning/inputs.resolve_inputs` and pin them: for each port, the current completed run of the selected live upstream node, with the ref from its `outputs`.
2. **Start (T4):** `GraphStore.start_node(node_id, accepted_by_change_id, resolved_inputs, prompt?, spec_digest?)` creates the run (lazily, D8) and attempt 1, or a retry attempt. The run gets a frozen `execution_policy` (the manifest limits, `resumable`, `gate_timeout_s`, `seed_from` from the accepting change, plus the session's `privacy_mode`) and its `target_version`. A refusal (not ready, a stale definition, another active attempt) makes the Executor re-read the graph.
3. **Context:** a `ToolContext` with `run_id` and `attempt_id`. Every write the agent or the tool makes presents the D24 attempt path.
4. **Agent Node:**
   - mark the attempt `running` (with `dispatched_at`) before spawning, and call `scratchpad_register_agent` (a harness tool);
   - `engine.run(spec, task=node.params.task, inputs, ctx, cancel)`, with a **"Node inputs"** section rendered from the pinned summaries;
   - **a retry attempt of a `resumable` run** calls `engine.run(..., resume=Resume.continue_())`, so it continues from the run's last checkpoint (thread `run_id`), or from the copied `seed_from` checkpoint on attempt 1 of a seeded run. With no checkpoint yet, it starts normally;
   - run `completion_check`.
4a. **Suspend and resume (T10, agent nodes only):**
   - `engine.run` returns `SUSPENDED` → `suspend_attempt(suspension)`: the attempt becomes `suspended`, its `llm` pool slot is released, and the task ends. No process waits, and nothing is held in memory;
   - a gate decision (`gate_resolved`) or the end of a pause wakes the Executor (Phase 10.2), which calls `resume_attempt` and then `engine.run(..., resume=Resume(interrupt_id, value))` (for a pause: `Resume.continue_()`) with the **same** attempt. A suspension is not a new attempt (I8);
   - a fencing refusal on resume (the run was superseded or cancelled meanwhile) ends the task without running anything.
5. **Tool Node:** `dispatch.invoke(spec, params + input refs, ctx, cancel)`: inline (marked `running` first) or a job (the worker marks it `running`, Plan 5).
6. **Outputs:** call each output port's `extract` → `{type, ref:{table, ids}, summary, phi}`.
7. **Finish (T6):**
   - success → `complete_attempt(outputs, usage)`. The Application API checks the refs, evaluates the node's expectations and may set `downstream_hold`;
   - failure → `fail_attempt(error_code, usage)`, with `error|timeout|oom|interrupted|cancelled|max_iterations|resource_unavailable|checkpoint_incompatible`.
8. **Events:**
   - the durable `node_status` and `run_attempt_*` events come from the Application API transactions;
   - the `on_result` view events come from dispatch;
   - heartbeats live in memory only and are never persisted (D19).
9. **In-node user gates** (cohort approval, segmentation review, tool approvals): the tool calls `ctx.user_gate()`, the attempt is suspended (step 4a), and the node shows `waiting_for_user` from the durable `run_attempt_suspended` event and the derived activity `awaiting_user`, so the wait survives a refresh and a restart. The user's decision is written through the D24 `user gate` path together with T10's gate decision. The live events `agent_waiting_for_user_input` / `agent_waiting_resolved` are replaced by `run_attempt_suspended` / `gate_resolved`.

## Phase 10.2 — Executor loop (`execution/executor.py`)

There is one `SessionExecutor` task per session with work. It is sequential, which is a current-phase constraint:

```
while True:
    node = graph.next_ready()     # previous live node's current run completed, no active downstream_hold,
                                  # session active, no non-terminal attempt in the session
    if (s := graph.resumable_suspension()):   # a suspended attempt whose gate is decided, or whose pause ended
        if pools.try_acquire(s.node.resources): spawn(node_runner.resume(s))
        else: await pool_released
        continue
    if node is None:
        await next(completion | graph_changed | pause_changed | stop_confirmed | gate_resolved); continue
    decision = guardrails.check(NodeStart(node))        # budget, resources, sandbox runtime
    if decision is not allowed:
        publish(node_start_refused(node, decision))     # runtime only, nothing stored (D19)
        await next(graph_changed | pool_released | probe_ok); continue
    if not pools.try_acquire(node.resources):
        await pool_released; continue
    spawn(node_runner.run(node))
```

- **Pools:** `execution/resources.py` gives each pool (`llm`, `cpu_heavy`, `gpu`, `monai_inference`, `orthanc_io`) an asyncio semaphore sized from `config/resources.yaml`. They are runtime only; the Celery queues remain the real limit for jobs.
- **No cache lookup:** reuse is the unaffected prefix that keeps its current runs (Appendix P §P.10).
- **Suspended attempts:** a `suspended` attempt is non-terminal, so no other node starts in the session while it waits (the session-wide index, a current-phase constraint). That is the same behaviour as a blocking gate today, but with no process. `gate_resolved` wakes the Executor, not the Main.
- **Important events** (system §22): a node fails after the automatic retries, `expectation_violated`, or `graph_idle`. A normal success is persisted and shown; it does not wake Main. The inbox reads them from the durable log (Plan 12).
- **Lifecycle:** the Executor exits when nothing is ready, and starts again on the next `graph_changed`. The session becomes `idle` (derived), never "finished" (§28).

## Phase 10.3 — Expectations

The Application API evaluates the node's `expectations` inside T6, against `outputs[*].summary`. A violation (e.g. `cohort_size < 30`):
- sets `downstream_hold='expectation_violated'` on **that run**, and publishes `expectation_violated`, which goes to the inbox;
- keeps the next live node not ready until a change releases the hold (`ReleaseHold`) or reruns the node. Inserting or moving nodes never moves the hold.

`execution/expectations.py` holds only the shared evaluator, used by the Validator (`BAD_EXPECTATION`) and by `InMemoryGraphStore`.

## Phase 10.4 — Live changes

On `graph_changed`:
- T2 has already superseded the affected runs and set `cancel_requested_at` on a running attempt. The Executor cancels the work (the cancel token for agent nodes, `JobClient.cancel` for jobs; MONAI cancel arrives in Plan 15) and confirms the stop with T7. A `suspended` attempt has no work to stop, so T7 confirms it at once.
- The next start waits until the superseded attempt is confirmed stopped (the session-wide active-attempt index), so a superseded run never runs beside its replacement.
- Affected nodes run again as new runs; new nodes are picked up. Work added after the graph went idle simply starts a new Executor.

## Phase 10.5 — Watchdog (`execution/watchdog.py`)

1. **What it watches:**
   - **agent nodes:** an in-memory heartbeat (`NODE_HEARTBEAT_TIMEOUT_S`, fed by the engine's `updates` stream) and `timeout_s` from the frozen policy, counted from the latest of `started_at` and `resumed_at`, so time spent suspended never counts;
   - **suspended attempts:** no heartbeat, because no process runs. A gate suspension older than the policy's `gate_timeout_s` (when set) gets a T7 cancel request with `error.code='gate_timeout'`; a pause suspension never times out;
   - **job nodes:** the job record (`heartbeat_at`, `TIMED_OUT`, `INTERRUPTED`, `error_code="oom"`).
2. **Timeout:** a cancel request with `error.code='timeout'` (T7). The attempt becomes `failed(timeout)` only once the stop is confirmed (D9).
3. **Automatic retries** (deterministic, not LLM): a new attempt through T4, only for `interrupted` and `resource_unavailable`, only while `attempt_number < execution_policy.max_attempts`, and, for an attempt with `dispatched_at` set, only when the policy says `idempotent`. **For a `resumable` agent run**, the run counts as idempotent unless the latest checkpoint's `pending_tools` lists a tool with `idempotent=False`: every finished tool call is stored and will not run again, so only the pending calls matter (T4 rule (f)). All other failures go to the inbox.

## Phase 10.6 — Control and restart

1. **Pause:** `control_state='paused'`. No new start (T4 requires `active`). A running agent node reaches its next `gate` node, `interrupt()`s and is **suspended** (`kind: pause`, T10), so a pause holds no process and survives a restart; jobs cannot pause, so they finish or are cancelled. The activity shows `pausing` until no attempt is `preparing`, `submitted`, `running` or `unknown`.
2. **Resume:** `active` again; the Executor resumes a pause-suspended attempt (T10). **Stop:** T7 on the active attempt (confirmed at once when it is `suspended`); the run becomes `cancelled` after confirmation, and the session `idle`. A later explicit user retry of a cancelled run opens a new attempt (T4 rule b); for a `resumable` run that attempt continues from the last checkpoint.
3. **Restart recovery** (in the app lifespan; Plan 17 wires it): T5 for every non-terminal attempt:
   - `suspended` → unchanged: nothing was running, and the checkpoint holds the state. It resumes when its gate is decided or its pause ends;
   - `in_process` / `inline` → `interrupted`. A `resumable` agent run then gets a retry attempt (Phase 10.5) that continues from its last checkpoint instead of starting over;
   - job attempts are re-attached by `submission_key`, resubmitted when still `preparing` and unknown to the backend, `record_lost` when the record is gone after dispatch, and `unknown` when the backend is unreachable.

   Then the Executor restarts the sessions that have ready or resumable work, and an `execution_resumed` notification is sent.

**Tests (`tests/unit/execution/`,** using `FakeAgentEngine`, the in-memory `JobClient` and `InMemoryGraphStore`):
- A→B→C readiness in order (§12); the Executor keeps running while the inbox is busy (§25);
- the pins: B consumes A's run 1 even after A is rerun in the middle;
- cancel of an affected running node; the next start waits for the confirmed stop;
- the expectation hold on the violating run, and its release by `ReleaseHold`;
- the watchdog: timeout as a cancel request, OOM, interrupted; retry only within the policy, and the `dispatched_at` rule;
- the pause gate; restart reconciliation for every attempt state; `node_start_refused` for a pool or budget refusal;
- **suspension:** a gate suspends the attempt and frees its pool slot; `gate_resolved` resumes the **same** attempt; a pause suspends and the end of the pause resumes; a restart leaves `suspended` attempts unchanged and they still resume; T7 on a suspended attempt confirms at once; `gate_timeout_s` cancels with `gate_timeout`; `timeout_s` ignores suspended time;
- **resume after interruption:** an interrupted `resumable` run is retried automatically and continues from its checkpoint; it is not retried when `pending_tools` holds a non-idempotent tool; a seeded first attempt starts from the copied checkpoint;
- every write carries the D24 attempt path.

**Done when:** a 3-node graph runs to completion on fakes, a gated node suspends and resumes across a simulated restart, and every rule above has a passing test.

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

   | Rule                                                                                                                                  | Outcome                                      |
   | ------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
   | Schema / Validator errors                                                                                                             | `invalid` (with the codes)                   |
   | The caller's visibility does not include the tool                                                                                     | `permission_denied`                          |
   | Main calls a tool directly that is not `inline`, or that is `heavy`                                                                   | `invalid: REQUIRES_NODE`                     |
   | Sandbox `runtime_check` (Plan 4)                                                                                                      | `permission_denied` / `resource_unavailable` |
   | A pool or queue is not alive (`cubeai:queue:*:alive`, MONAI health)                                                                   | `resource_unavailable`                       |
   | Budgets: session tokens (`SESSION_TOKEN_BUDGET`), active nodes, `max_attempts`                                                        | `permission_denied: BUDGET_EXCEEDED`         |
   | Mandatory approvals: `destructive=True`, `trust != first_party` (unless the tool is in the MCP `auto_approve` list), `generated_code` | `approval_required`                          |
   | Plan mode (Phase 11.2)                                                                                                                | `approval_required: PLAN_MODE`               |
   | PHI / privacy (Plan 13), external access (Plan 14)                                                                                    | `permission_denied`                          |

4. **Test:** a unit test asserts that no rule reads the request text, counts steps, or looks at `cost` to pick a strategy.

## Phase 11.2 — Modes (Plan / Auto)

- `execution_mode` is stored on the session (`PATCH /session/{id}/settings` on the Agent System, which calls the Application API). **The default is `auto`** (decided 2026-10-01). Plan mode is opt-in, for users who want to approve every piece of work. The mandatory reasons apply in both modes.
- **Approval reasons are computed when a change is proposed** (Appendix P D7), never when a node starts:
  - `PLAN_MODE`: the session is in Plan mode and the change adds, modifies, reruns or releases work. Removing nodes that never started, and cancelling, need none. The Application API adds this reason itself;
  - `supersedes_completed`: the change would supersede a completed run (D22). The Application API adds this reason itself;
  - `destructive`, `generated_code`, `untrusted_tool`, `approval_required:<code>`: from the target manifests, computed by the guardrails here.
- A change with no reasons is applied at once (T2). A change with reasons is proposed (T1) and waits for a user decision.
- Main's direct calls to `side_effects="writes"` tools are promoted to an `AddNode` change before any effect (D21). Read-only calls are always allowed.
- Start-time conditions (budget, resources, the sandbox runtime check) are never approvals: the Executor refuses the start and publishes `node_start_refused` (D19).

## Phase 11.3 — Approvals (`harness/approvals.py`)

1. **There is no `approvals` table.** The pending `graph_changes` row is the approval (Appendix P D7), and a session has at most one. A new proposal withdraws the previous one; past `APPROVAL_TTL_H` it becomes `expired`.
2. **When a change needs approval:** T1 stores it, writes the Main's `tool_result`, and publishes `graph_change_proposed` + `approval_requested`. The caller gets `RunAccepted{node_id, change_id, status: "awaiting_approval"}` (or the `graph_apply` result).
3. **Deciding** is done only by the user, never by an LLM, and always quotes the digest:
   - REST: `POST /session/{id}/changes/{change_id}/decision` with `{decision: approve|reject, content_digest, comment}`. The Agent System forwards it to the Application API (`/graph-changes/{change_id}/decision`);
   - deterministic chat commands, handled before the LLM in the input route: `/approve [change_id]`, `/reject [change_id] [reason]`, and the legacy text `APPROVE` when exactly one change is pending (the current canvas buttons send it). A command uses the digest of the pending change the user was shown.
4. **On approve:** T2 under CAS. If the base moved, the change becomes `stale`. **There is no auto-rebase**: Main is notified (`graph_change_stale`) and re-proposes. The change also goes `stale` if a derivable reason changed meanwhile (for example a run completed).
5. **Every decision** → a durable event and an inbox notification.
6. **Tool approvals inside a node** (D27; they replace the in-memory in-run approvals of D19): when a `ToolCall` made by an agent inside a node gets `approval_required` (an untrusted MCP tool, a `destructive` tool), the `before_invoke` hook raises a `tool_approval` gate through `ctx.user_gate("tool_approval", {tool, args_digest, reason})` instead of refusing. The attempt is suspended (T10) and the user decides with `POST /session/{id}/runs/{run_id}/gate-decision` (Phase 12.7), which the Agent System forwards to the Application API. Approve → the call runs on resume; reject → the call returns `error_result("approval_rejected")` to the agent. Like every gate, it survives a restart. Graph-change approvals (items 1–5) are unchanged and never use `interrupt()`.

**Tests (`tests/unit/harness/test_guardrails.py`, `test_approvals.py`):**
- each rule outcome; order and short-circuit;
- a new session is in Auto mode;
- Plan vs Auto on the same change; removals allowed in Plan mode;
- mandatory reasons in Auto mode; `PLAN_MODE` and `supersedes_completed` added by the store even when the caller omits them;
- the chat commands never reach the LLM; `APPROVE` with 0, 1 or 2 pending changes;
- a wrong digest is refused; `stale` on a moved base; expiry;
- an agent's `approval_required` tool call suspends the node as a `tool_approval` gate; approve runs the call once on resume, reject returns `approval_rejected` to the agent; a Main direct call that needs approval is still promoted to an `AddNode` change (D21), never a gate;
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
2. **It replaces `ConnectionManager.message_queues`** (`socket_store.py:86-98,187,224`). The inbox lives in memory, but every item is durable first: `POST /session/{id}/input` calls T3 on the Application API and enqueues only after the commit (D14); notifications come from waking events in the durable log. The handled position is `sessions.inbox_handled_through_event_id`, advanced by T8 only after the reply and the changes have committed. On restart, and periodically, the inbox re-reads the waking events above the cursor.
3. **`POST /session/{id}/input`** turns `/skills`, `/approve` and `/reject` into Commands, handled without the LLM. Anything else becomes a `UserMessage`.
4. **Coalescing:** notifications that arrive during a Main turn are merged into one message for the next turn.

## Phase 12.2 — Control tools (`toolpacks/control/`, `visibility={"main"}`, inline)

All control tools are `light`, except `plan` and `recover`, which are `medium` with a hard timeout.

| Tool                                                          | Does                                                                  | Returns                                                                           |
| ------------------------------------------------------------- | --------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `registry_search(query, kind?)`                               | Lexical search over `catalog.index("main")`                           | Compact entries                                                                   |
| `registry_describe(name)`                                     | `catalog.describe`                                                    | Schema, ports, manifest                                                           |
| `graph_inspect(node_id?)`                                     | The graph summary, or one node's details (error, log tail, artifacts, the step timeline from its checkpoints, a pending gate) | Summary                                                                           |
| `execute(target, params, input_sources?, expect?)` | A one-node change appended to the sequence (or inserted with `at`), through the Guardrails and the Diff Engine | `RunAccepted {node_id, change_id, status: applied \| awaiting_approval}` |
| `graph_apply(diff \| proposal_id, base_version, on_running?)` | Applies (T2) or proposes (T1) a change | `{version, change_id, status: applied \| awaiting_approval, affected, errors}` |
| `node_cancel(node_id)` | Requests cancellation of the node's active attempt (T7); a node that never started is removed with a change | Status |
| `node_message(node_id, text)`                                 | Adds a user interruption to an agent node: into the in-memory mailbox while it runs (the `gate` node moves it into the checkpointed state), or with `aupdate_state` on the run's thread while it is `suspended`, so the message is durable either way | Ack                                                                               |
| `artifact_read(node_id \| run_id, port)` | One port of a run's `outputs`: the summary and the ref (never the raw data) | Summary |
| `skill_load(name)`                                            | The skill body                                                        | Text                                                                              |
| `plan(request, focus_node_ids?)`                              | Phase 12.5                                                            | `GraphProposal`                                                                   |

`execute` and `graph_apply` **never wait for execution**. They return as soon as the version is applied or proposed (§14).

Their `tool_result` row is written by the Application API in the same transaction as the change (D21), and their idempotency key is `sha256(turn_id‖call_id)`, so a re-handled turn never submits the same work twice.

## Phase 12.3 — Context Manager (`harness/context_manager.py`)

1. **Main's context** (§32):
   - the system prompt from `main_agent/agent.md`;
   - the recent chat from the Application API, including the `tool_calls` / `tool_result` rows, ordered by `seq`;
   - the latest `context_summary` system message of the chat, and a compact graph summary (nodes shown as `n<node_seq>`);
   - the skill index; the compact registry index (`catalog.index("main")`);
   - the schemas loaded so far (`registry_describe` results).
2. **Compaction:** above `MAIN_CONTEXT_TOKEN_BUDGET`, the older turns are summarized with the `summarizer` profile and saved as a `role='system'` message `{v, kind:'context_summary', through_message_id}` in the chat (Appendix P; there is no `sessions.context_summary`). Tool results marked `phi: true` are kept out of a context sent to an external model.
3. **The Composer's context:** the request, the current graph, the relevant registry details (search hits + described entries), the skills with `applies_to: composer`, and the artifact types.
4. **An execution agent's context:** its own prompt, the node inputs, the node instructions (`params.task`), its own tool schemas and mailbox messages. It is built once, at the first start of the run; after that the agent's context **is** its checkpointed state, which a resume continues and the engine's `compact` node keeps under `max_context_tokens` (Phase 9.4). The PHI rule of item 2 applies to that compaction too.
5. **The Main is not checkpointed** (D21): its context is always rebuilt from the chat, as in item 1.

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
5. **Persistence of a turn:** the `tool_calls` row is committed before any call runs, and each direct `tool_result` as soon as its call ends. After a restart, calls without a result are closed as `interrupted` before the input is handled again. The turn ends with T8 (the cursor and the token usage).

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
   - `POST /session/{id}/changes/{change_id}/decision`;
   - `PATCH /session/{id}/settings`;
   - `POST /session/{id}/nodes/{node_id}/cancel`;
   - `POST /session/{id}/runs/{run_id}/gate-decision` (a `tool_approval` gate; the cohort and segmentation gates keep their domain routes) → the Application API T10;
   - `GET /session/{id}/runs/{run_id}/steps` (the step timeline from the run's checkpoints, for debugging and the future canvas).
2. **Register the new events in `services/event_registry.py`:**
   - **durable** (published by the Application API transactions, registered here for classification): `user_input`, `graph_change_proposed/applied/rejected/stale/expired`, `node_run_superseded`, `run_attempt_started/ended`, `run_attempt_suspended/resumed`, `gate_resolved`, `node_status`, `expectation_violated`, `node_start_refused`, `graph_idle`, `session_state`, `approval_requested`;
   - **ephemeral:** progress keeps using `tool_progress`, and streaming keeps using `stream_chunk`.
3. **Update `_SOURCES`** in `tests/unit/services/test_event_registry.py` to scan `harness/`, `execution/`, `planning/` and `toolpacks/`.

**Tests (`tests/unit/harness/`):**

- inbox order and dedupe; commands skip the LLM; coalescing; a restart re-reads the waking events above the cursor; T8 runs only after the reply commits;
- **a Main turn with a fake model:** `execute` returns `RunAccepted` while a fake node runs, and a second user question is answered before the node finishes (§6);
- `plan` never applies anything; the repair rounds;
- context contents and compaction; the skills index;
- `node_message` to a running node and to a suspended node both reach the agent after resume; the Main turn uses no checkpointer;
- the routes, including the gate decision and the steps timeline; the event registry scan.

**Done when:** on fakes, a user message → Main → `execute` → Executor → `node_status` done → `graph_idle` notification → Main reply works in tests.

---

# Plan 13 — Recovery and Privacy

**Goal:** system Phase 3: the Recovery Agent, resource failure handling and PHI-Local.

## Phase 13.1 — Recovery Agent (`execution/recovery.py`, control tool `recover`)

1. **`recover(node_id)`** = `make_agent_tool("recover", "recovery_agent", RecoveryProposal, post=validate)`. It is visible only to Main.
2. **Its context:** the node spec and params, the `error_code`, the log tail (the job dir or the agent run notes), the graph neighbourhood, and for an agent node the step timeline of the failed run (`/runs/{run_id}/steps`: each step's node, tool calls and errors, with checkpoint ids). It reads summaries and step metadata, never raw checkpoint blobs.
3. **What it returns:** a diagnosis, an optional validated `GraphDiff` (e.g. new params, a smaller cohort, another model) and a message for the user. It **never applies anything**; Main decides.
   - **Time travel:** when the failure came late in a long agent node (for example a bad tool argument at step 9 of 10), the diff may be a `RerunNode(seed_from={run_id, checkpoint_id})` that starts the new run from the step before the mistake, with a correction in `params.task`, instead of repeating the whole node. It is still a new run that supersedes the old one, and T4 starts fresh when the checkpoint is gone or incompatible (D27).
4. **Order:** the deterministic retries of Phase 10.5 always run first. Only failures that remain reach Main.

## Phase 13.2 — Resource failure handling

1. **Mapping to `resource_unavailable`:**
   - a queue with no live worker;
   - MONAI 429 after the `unavailable_s` budget (Plan 15);
   - a GPU queue that is declared but not started;
   - Orthanc or Application API health failures.
2. **What happens:** nothing is stored on the node (D19). At start, the Executor refuses the node and publishes `node_start_refused`, and it checks again before every start. During a run, the attempt fails with `resource_unavailable`, and the Watchdog retries it through T4 within the policy. A notification goes to the inbox.
3. **Automatic retry:** the Watchdog probes the refused pools every `RESOURCE_PROBE_S` and wakes the Executor when the resource returns. This is a technical rule, not a strategy.

## Phase 13.3 — PHI-Local (`harness/privacy.py`)

1. **The setting:** `privacy_mode` (`standard|phi_local`) is set per session.
2. **In `phi_local`:**
   - the Model Gateway `locality_filter` keeps only `locality: local` models. If there is none, the Guardrails return `permission_denied: NO_LOCAL_MODEL`.
   - `config/models.yaml` marks `gpt-oss:120b-cloud` as `external`, and ships a commented `local_default` Ollama entry.
3. **PHI labels:** output ports with `phi=true` (in `node_runs.outputs`), tool results with `phi: true` (in the chat), and tools with PHI scopes mark their content. The Context Manager never puts PHI-marked content into a context that goes to an external model. It sends refs and non-PHI summaries instead.
4. **In `standard` mode:** access is normal, as configured.
5. **Everywhere:** the MCP edge refuses PHI args (Plan 14), and the audit logs hash patient identifiers.
6. **Frozen per run:** each run freezes `privacy_mode` in its `execution_policy`. Switching a session to `phi_local` requests cancellation of every active attempt frozen as `standard`, in the same transaction (T7); a `suspended` one is cancelled at once, so it can never resume under the old mode.
7. **Agent checkpoints (D27):** they hold the agent's messages, so they may hold PHI. They stay in the Application API's PostgreSQL only, are never sent to the MCP edge or to an external model as raw blobs, and are purged `AGENT_CHECKPOINT_RETENTION_DAYS` after the run ends. A `seed_from` copy requires the same `privacy_mode`, and the in-node compaction obeys the same locality filter as the agent's model.

**Tests:**

- recovery returns a valid diff or none, and never applies it; a seeded rerun proposal names a checkpoint of the failed run of the same node;
- a refused start is retried when the probe succeeds; a `phi_local` switch cancels the `standard` attempts, including suspended ones; a seed across privacy modes starts fresh;
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
   - `approval_required` unless the tool is listed in `auto_approve`. Inside an agent node this becomes a durable `tool_approval` gate that suspends the node (Phase 11.3 item 6);
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

- J1, J2, J4 (server side), J6, J8, J10, J11, J12. J1 is also the prerequisite of Appendix P §P.9 Phase 3: until it lands, a failed segmentation pair cannot be re-executed within the same run.
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
- The pair job id follows D10: `inference_job_id = sha256(batch_correlation_id|pair_id|model|prompt)`, with the correlation id stored once in `segmentation_batches.config`. Every attempt of a run re-attaches to the same jobs, and a new run gets new ones (S5, `module.py:1439-1441`).
- The heartbeat in `pipelines/common.py`: J14, S2.
- Use `to_thread` for heavy numpy / SimpleITK work (S7).
- The session handle is passed in explicitly (S9); the Executor's cancel token reaches the MONAI cancel.
- **S10 is solved by removing the wait:** the agent path no longer calls `_wait_for_review`. The tool runs the pair work inside `ctx.durable(...)` and then suspends on `ctx.user_gate("segmentation_review", …)` (Phase 9.7), so no request polls and a restart during review loses nothing. `_wait_for_review` is deleted with its last caller, or keeps `RetryPolicy` if a non-agent caller remains.
- The `toolpacks/segmentation/run_segmentation.py` tool also uses `MonaiHttpBackend`.

**Tests (`tests/unit/monai/`, `tests/unit/segmentation/`):**

- J1 resubmit + slot count; J2 slot kept until the work ends;
- `INTERRUPTED` handling; the TTL sweep; the status-string contract;
- backend tests with `respx`, including the 429 → `resource_unavailable` mapping;
- the heartbeat never leaks the inner task;
- a finished result is kept after a long pause;
- a node cancel reaches MONAI;
- the review gate: the tool suspends after the pairs finish; a resume after a simulated restart does not resubmit any pair job and reads the review decision from the domain rows.

**Done when:** the J1 test is green, both clients use `MonaiHttpBackend`, and the segmentation review is a gate.

---

# Plan 16 — Other Job Systems

**Goal:** fix the remaining long waits, training, ingestion and background tasks.

## Phase 16.1 — Long waits

- **S11:** the agent path no longer long-polls (Phase 15.4). If another caller of `/segmentation-review/wait` remains, the server is keyed by batch, leaks no waiters, and checks for disconnects; otherwise the endpoint is retired in Plan 17.
- **S13 (server side):** compile before taking the row lock, with `asyncio.create_subprocess_exec` in its own process group, killed on timeout.
- **S13 (client side):** `latex_to_pdf` retries and resolves the 409 `stale_version`.
- **S8:** PATCH only the changed pair.

## Phase 16.2 — Training

Fix T1–T9. T1 becomes one server-side training job (prepare → create-model → train), orchestrated by the Application API. Its receipt is a `training_jobs` row committed before the POST (Appendix P D17, transaction T9); the table is created by migration "p" in Plan 6. Training stays outside the Session Graph; it belongs to the MONAI training UI.

## Phase 16.3 — Ingestion on Celery

Ingestion conversion becomes a Celery job (`job_name="ingest_convert"`, registered as a `harness`-visible job tool). This fixes G2, G3 and G6 by design. Then fix G1, G4, G5, G7 (one frontend upload client) and G8.

## Phase 16.4 — Agent System tasks

- **K1:** use `best_effort.schedule_best_effort`.
- **K2:** stop ordering, applied to the new `SessionRuntime` stop: set the status only after the tasks end, and re-raise `CancelledError`.
- **K3:** `reload` only when `APP_ENV=dev`.

**Tests:**

- S11 waiter cleanup; S13 does not block the event loop (a fake slow compiler);
- the training job state machine (T9: receipt before the POST; reconcile 404 → resubmit the same id; unfreeze only when no active receipt remains);
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
   - build `HttpCheckpointSaver` and give it to `LangGraphAgentEngine`; start the checkpoint purge sweep (`AGENT_CHECKPOINT_PURGE_S`, Phase 9.5);
   - start `SessionRuntimeManager` (restart recovery, Phase 10.6, including resuming the decided gates of `suspended` attempts) and the MCP manager (Plan 14);
   - keep `ensure_storage_layout()` and `install_write_guard()` after every import.
2. **`POST /session/{id}/input`** → the Session Inbox (Plan 12).
3. **`/pause`, `/resume`, `/stop`:** the same paths and bodies, now driving `SessionExecutor` control (Phase 10.6).
4. **Rebuilt from graph state:** `GET /status/{id}`, `/session/{id}/execution-state` and `/session/{id}/recover`.
5. **Retired, returning `410 Gone` with a short message:** `/plan`, `/run`, `/session/{id}/resume-pipeline`. The frontend update belongs to the Surface plan.
6. **Application API:** the persistence cut-overs already happened in Plan 6 (no dual path, D26). What is left: a final migration drops `sessions.intent` and `session_graphs.approved_at` (read-only legacy columns), and `approve_plan` / `create_pending_runs` and `CANONICAL_AGENT_TYPES` are deleted (the producer allowlists stay, checked against the catalog). **No table is dropped.**

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
- No retired schema name (the rename map of Appendix P §P.2) outside migrations, old durable events and the problem catalog. The Phase 6.1 source scan stays in the deterministic suite.
- No `langgraph.checkpoint.postgres`, `langgraph.checkpoint.sqlite`, `PostgresSaver`, `langgraph_sdk` or `langgraph.store` import, and no `BaseCheckpointSaver` subclass other than `HttpCheckpointSaver` (D1, D27).
- No `langgraph` import in `toolkit/` outside `toolkit/engines/` (`test_boundary.py`), and no checkpointer passed to the Main, the Composer or the Recovery Agent.
- No caller of `_wait_for_review` or `/segmentation-review/wait` on the agent path, and no `agent_waiting_for_user_input` emitter (replaced by `run_attempt_suspended`).

## Phase 17.5 — Docs

1. **`CLAUDE.md`:**
   - the service table (Redis, compute worker, compute beat);
   - the "Agent pipeline" section, rewritten as the harness (Main Agent, Session Graph, Executor, Guardrails, modes);
   - LangGraph-native agent nodes: `HttpCheckpointSaver` (`thread_id = run_id`), gates and pause as suspended attempts, `ctx.user_gate` / `ctx.durable` / `ctx.durable_map`, workflow agents and `Send`, in-node compaction, `seed_from`, and the rule that the Main has no checkpointer;
   - the Toolkit (`ToolSpec` / `AgentSpec` / `run_agent`, packs, catalog, `agent.md`, skills);
   - the MCP edge; the sandbox; the new env vars;
   - the modeling engine (scikit-learn, no PyCaret) and the renamed modeling tools (Python 3.13 itself is documented in Plan 0);
   - replace the "no GPU lease / no `WAITING_FOR_RESOURCE`" note with the runtime resource pools and `node_start_refused` (nothing about resources is stored);
   - the single-worker rule still holds.
   - the database: the Appendix P vocabulary, the D24 write paths, the transactions T0–T10, the agent checkpoints and suspended attempts (D27), and the current-phase constraints;
2. **`cubeai-docs/`:**
   - `agent-system/harness.md` (the architecture above);
   - `agent-system/agent-checkpoints.md`: checkpoints, gates, pause, resume, `seed_from`, retention, and the `langgraph` upgrade procedure (Phase 9.1);
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
3. Identity comes only from `current_context()`: no identity env reads, no module constants. Every write on the execution path sends the D24 path from the context.
4. No module-level mutable state. Settings are read lazily and validated.
5. Expected failures return `error_result`. No traceback reaches the LLM; it is logged instead.
6. HTTP goes through the shared client with explicit timeouts.
7. `inline` tools do no sync work longer than about 50 ms on the event loop (use `asyncio.to_thread`).
8. Idempotent, or marked `idempotent=False`. `job` tools take JSON args only.
9. Temp files go only in per-call / per-job dirs from `storage_paths`.
10. There is a unit test with a fake context: one success and one error.
11. The name is unchanged. The only exceptions are `save_literature_output` (Plan 2) and the 5 renamed modeling tools (Plan 7).
12. It runs on Python 3.13 and imports no `pycaret`.
13. **Gates (D27):** a tool that calls `ctx.user_gate()` runs every side effect before the gate inside `ctx.durable()` (LangGraph runs the interrupted call again from its start on resume), and its gate payload holds refs and summaries only. It has a test that suspends, resumes, and checks that the durable step ran once.
14. **Results in messages:** what a tool returns to the model is small (refs, ids, summaries), because it is stored in the agent's checkpoints. Large data goes to typed rows or `stored_files`.
15. **Progress** is reported through the engine's custom stream (`get_stream_writer()`, wrapped by the toolkit event sink), never by polling or by writing a workspace file.

## Definition of Ready — every agent

1. It has an `agent.md` that the loader validates, and a `kind`.
2. Every name in its allowed tools exists and is visible to agents. Every delegate is `internal`.
3. Execution agents declare their ports and read upstream data from "Node inputs".
4. `completion_check` / `output_contract` are set where the output feeds other nodes.
5. There is a test run on `LangGraphAgentEngine` with a scripted fake model.
6. **A `resumable` agent has a resume test:** the run is stopped after a tool call, resumed from its checkpoint, finishes, and no finished tool call runs twice. An agent with `gates` also has a suspend/resume test for each gate.
7. Its `max_context_tokens` is set, or the default is accepted on purpose; a `workflow` agent has a test of its graph with fake models, including a fan-out resume.

## Testing (the work only — no end-to-end run)

| Suite                          | Command                                                                                                                                                                                                                                                                         |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Runtime (Plan 0)               | `uv pip compile Backend/agent_system/requirements.txt --python-version 3.13 --python-platform x86_64-manylinux_2_28`, then `python -m pytest -c Backend/pytest.ini Backend/tests/runtime -m docker` against the built agents image                                              |
| Modeling engine                | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/toolpacks/modeling`                                                                                                                                                                                                  |
| Deterministic gate             | `powershell -NoProfile -ExecutionPolicy Bypass -File Backend/tests/run_tests.ps1 -Suite deterministic`                                                                                                                                                                          |
| Toolkit / agents / engines     | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/toolkit Backend/tests/unit/agentpacks Backend/tests/unit/engines`                                                                                                                                                    |
| Tool packs                     | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/toolpacks Backend/tests/unit/functions`                                                                                                                                                                              |
| Harness / planning / execution | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/harness Backend/tests/unit/planning Backend/tests/unit/execution`                                                                                                                                                    |
| Sandbox                        | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/sandbox` (add `-m docker` for real containers)                                                                                                                                                                       |
| Jobs (unit)                    | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/jobs`                                                                                                                                                                                                                |
| Jobs (integration)             | `docker compose --env-file deploy/.env -f deploy/docker-compose.backend.gpu.yml -f deploy/docker-compose.backend.dev.yml up -d redis compute-worker-cpu compute-beat`, then `python -m pytest -c Backend/pytest.ini Backend/tests/integration/jobs -m "integration and celery"` |
| State store (integration) | Start `postgres` + `db-migrate` with the same compose files, then `python -m pytest -c Backend/pytest.ini Backend/tests/integration -m "integration and docker" -k "graph or persistence or checkpoint or suspend"` |
| Checkpointer conformance | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/execution/test_checkpointer.py Backend/tests/unit/engines -k "checkpoint or resume or gate"` |
| Persistence contracts | `python -m pytest -c Backend/pytest.ini Backend/tests/contracts -k "schema or fencing"` |
| Schema contracts               | `python -m pytest -c Backend/pytest.ini Backend/tests/contracts`                                                                                                                                                                                                                |
| MONAI / segmentation           | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/monai Backend/tests/unit/segmentation`                                                                                                                                                                               |
| Storage boundary               | `powershell -ExecutionPolicy Bypass -File Backend/scripts/check_storage_boundary.ps1`                                                                                                                                                                                           |

**Final check:** `python -c "from agent_system.toolkit.catalog import build; c = build(); c.validate(); print(len(c.tools), len(c.agents))"` prints:

- **75 tools**: 55 moved + `run_segmentation_with_guard` + 6 literature + `run_agent` + `run_modeling` + 11 control tools (`registry_search`, `registry_describe`, `graph_inspect`, `execute`, `graph_apply`, `node_cancel`, `node_message`, `artifact_read`, `skill_load`, `plan`, `recover`);
- **16 agents.**

The MCP edge adds zero.

## Traceability

**system_plan → merged plans**

| system_plan                                                        | Where                                                                   |
| ------------------------------------------------------------------ | ----------------------------------------------------------------------- |
| §1–2 Core idea, control vs execution plane                         | Principles; Plans 10–12                                                 |
| §3 Surface                                                         | Not in this roadmap (the backend endpoints are ready: Plans 11.3, 12.7) |
| §4 Main Agent, Context Manager, Skills Loader, Inbox               | 3.4, 12.3, 12.6, 12.1                                                   |
| §4 Runtime Guardrails, Model Gateway, State, Observability         | 11, 9.2, 6, 9.2 usage + 10.1 timings (tracing later)                    |
| §5–6 Main decides, non-blocking loop                               | 12.2, 12.4                                                              |
| §7–9 Planner, Composer, Validator                                  | 3.4, 12.5, 8.2                                                          |
| §10 Runtime Guardrails                                             | 11.1                                                                    |
| §11–14 Execution, Executor, long-running agents, tracked execution | 10, 12.2 (`execute` → `RunAccepted`); long agent nodes resume and suspend: 9.4, 9.5, 10.1 |
| §15–16 Tool manifest, cost / side_effects                          | 1.1, 2.4, 7.1, 7.3                                                      |
| §17–19 Node, graph ≠ execution, live Session Graph                 | 6.1, 8, 10                                                              |
| §22 What wakes Main                                                | 10.2, 10.3, 12.1                                                        |
| §23–25 Main loop, Executor loop, independence                      | 12.4, 10.2                                                              |
| §26–27 Diff Engine, affected set, diff rules                       | 8.3, 8.4, 10.4                                                          |
| §28 No permanent finished workflow                                 | 6.1, 10.2                                                               |
| §29 Agents                                                         | 3 (modeling is the `run_modeling` tool, Plan 7)                         |
| §30 Skills                                                         | 3.5, 12.6                                                               |
| §31 Modes, Privacy                                                 | 11.2, 13.3                                                              |
| §32 Context                                                        | 12.3                                                                    |
| §33 Registry                                                       | 1.3, 12.2                                                               |
| §34 Folder structure                                               | Target layout (with the renames of decision 1)                          |
| §35 Phases 1–3                                                     | M3, M4                                                                  |
| §35 Phase 4                                                        | Not in this roadmap                                                     |
| §36 Evaluation                                                     | Not in this roadmap                                                     |

**tools_plan → merged plans**

| tools_plan | Merged                                                                                                   |
| ---------- | -------------------------------------------------------------------------------------------------------- |
| – (new)    | 0 (Python 3.13, PyCaret removed) and 7.0–7.2 (the scikit-learn modeling engine, renames, legacy records) |
| P1         | 1, plus 10.1 (RunHarness, DirectToolExecutor) and 6 (the CANONICAL contract replaced)                    |
| P2         | 2                                                                                                        |
| P3         | 3 (`modeling_agent` removed; control agents and skills added)                                            |
| P4         | 4                                                                                                        |
| P5         | 5                                                                                                        |
| P6         | 7 (on the new engine, with the 5 modeling tools renamed)                                                 |
| P7         | 14                                                                                                       |
| P8         | 15                                                                                                       |
| P9         | 16                                                                                                       |
| P10        | 17                                                                                                       |

**Persistence design (Appendix P) → merged plans**

| Appendix P | Where |
|---|---|
| D1, D2 (owner, users) | 6.2–6.4 |
| D3, D14, D21 (one chat, input, Main transcript) | 6.2–6.3 (T0, T3), 12.1, 12.3, 12.4 |
| D4–D7, D22 (graph, nodes, changes, approvals, one research line) | 6.2–6.4 (T1, T2), 8, 11 |
| D8, D9, D16 (runs, attempts, frozen policy) | 6.2–6.3 (T4–T7), 10 |
| D10 (segmentation fan-out) | 15.4 |
| D11, D23, D25 (results, input resolution, selection freeze) | 6.3, 8.3, 10.1 |
| D12 (backend state) | 5, 15 |
| D13, D15 (control state, settings) | 6.2, 10.6, 11.2, 13.3 |
| D17 (training receipts) | 6.2, 16.2 |
| D18 (stored files) | 6.2 |
| D19 (not persisted) | 10.2, 13.2 |
| D20 (vocabulary) | 6.1 |
| D24 (fencing on every write path) | 5.3, 6.2–6.4, 9.5, 10.1 |
| D26 (two cut-overs, no dual path) | 6.1, 6.2, 17.1 |
| D27 (agent checkpoints, suspended attempts, T10, `seed_from`) | 3.1, 6.2–6.5, 8.1–8.2, 9.3–9.7, 10.1, 10.2, 10.5, 10.6, 11.3, 12.2, 12.7, 13.1, 13.3, 15.4 |

---

## Appendix — Problem catalog

### A. MONAI inference jobs (Plan 15; J5 is handled through the Plan 10 pools and Plan 16)

| ID  | Problem                                                                                                                                                                                                                                                                                                                     | Fix                                                                                        |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| J1  | **Critical.** Resubmitting a job in a terminal non-success state calls `reserve()` (`inference_jobs.py:282`), then `_write_job(QUEUED)` (`:305`) hits the terminal guard (`job_root.py:133-143`) → 500, and the slot leaks. After 9 retries, all inference returns 429 until a restart. A normal Stop → Resume triggers it. | Explicit re-queue (`attempt + 1`); write the state before reserving; release on any error. |
| J2  | Cancel and timeout only close the loopback call; the GPU work continues, but the slot is freed.                                                                                                                                                                                                                             | Keep the slot until `/infer` returns; persist `CANCELLING` → `CANCELLED`.                  |
| J3  | (to verify) `/infer` may block MONAI's event loop.                                                                                                                                                                                                                                                                          | Check the 0.8.5 image; if it blocks, run the infer task in a thread.                       |
| J4  | Clients ignore `INTERRUPTED` (`services.py:301`, `run_segmentation.py:111`).                                                                                                                                                                                                                                                | Shared vocabulary; unknown = final; re-queue once.                                         |
| J5  | Training preparation uses the inference slot (`training_jobs.py:118`).                                                                                                                                                                                                                                                      | Named capacity pools (Plans 10, 16).                                                       |
| J6  | No server-side limit on queue wait.                                                                                                                                                                                                                                                                                         | `queue_timeout_s` → `TIMED_OUT(queue)`.                                                    |
| J7  | 429 is treated as permanent; `Retry-After` is ignored (`services.py:203-212`).                                                                                                                                                                                                                                              | `RetryPolicy`.                                                                             |
| J8  | Job files are never cleaned; `.result.tmp` is left after a crash.                                                                                                                                                                                                                                                           | TTL sweeper; start-up cleanup; purge in `finally`.                                         |
| J9  | `/inference/` exposes `/restart` and the job endpoints to the browser.                                                                                                                                                                                                                                                      | Deny them at the ingress; internal token.                                                  |
| J10 | Blocking file I/O on MONAI's loop.                                                                                                                                                                                                                                                                                          | `to_thread`; create directories once.                                                      |
| J11 | `CANCELLING` is never persisted.                                                                                                                                                                                                                                                                                            | Persist it.                                                                                |
| J12 | No progress, heartbeat, list or queue position.                                                                                                                                                                                                                                                                             | Add the fields and a list endpoint.                                                        |
| J13 | Two different MONAI clients; unscoped ids.                                                                                                                                                                                                                                                                                  | One `MonaiHttpBackend`; run-scoped ids.                                                    |
| J14 | The heartbeat wrapper leaks the inner task on non-cancel errors (`pipelines/common.py:158-226`).                                                                                                                                                                                                                            | `finally: cancel + gather`.                                                                |

### B. Segmentation client and long waits (Plans 15, 16; S12 in Plan 2)

| ID  | Problem                                                                                                                                               | Fix                                                                                             |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| S1  | The unavailable grace period only logs (`services.py:224-284`).                                                                                       | An enforced `unavailable_s` budget.                                                             |
| S2  | The budgets don't nest; the deadline counts pause time and is checked before `task.done()`.                                                           | Check `done()` first; pause-aware; outer budget = sum + margin.                                 |
| S3  | The result download is not retried, and it is fully buffered.                                                                                         | Streaming with retry.                                                                           |
| S4  | Purge happens only on success paths.                                                                                                                  | Purge in `finally` + TTL.                                                                       |
| S5  | Resume after a params change → 409.                                                                                                                   | A params digest in the job id.                                                                  |
| S6  | Many fixed-interval polls with different rules.                                                                                                       | `poll_until`.                                                                                   |
| S7  | Heavy numpy / SimpleITK work on the loop (`services.py:462,571,763`, `module.py:446-455`).                                                            | `to_thread`.                                                                                    |
| S8  | O(N²) checkpoint PATCHes (`batch_store.py:228-243`).                                                                                                  | PATCH only the changed pair.                                                                    |
| S9  | The cancel checks swallow `ImportError` / `KeyError` (`common.py:133-134,152-153`).                                                                   | An explicit session handle.                                                                     |
| S10 | `_wait_for_review` retries too few errors; a 5xx is fatal (`module.py:986-1015`).                                                                     | Removed on the agent path: the review is a `segmentation_review` gate (Phase 15.4). Otherwise `RetryPolicy` + an overall deadline. |
| S11 | The review long-poll is keyed by session, leaks tasks and ignores disconnects (`segmentation_review.py:682-705`).                                     | No agent caller after Phase 15.4. For any remaining caller: key by batch; cancel the waiter; `is_disconnected()`. |
| S12 | Cohort gate: one transient error ends the wait; the timeout is sleep-only; a global flag (`targets_matcher.py`).                                      | `poll_until` + per-session state.                                                               |
| S13 | The LaTeX compile blocks the Application API loop while holding a row lock (`repositories/reports.py:298-304,346-354,433`); the client doesn't retry. | Compile before the lock with `create_subprocess_exec`; the client retries and resolves the 409. |

### C. Heavy tools (Plan 7; H19 and H20 in Plan 5)

| ID  | Problem                                                                                                            | Fix                                                                                                         |
| --- | ------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| H1  | The model path depends only on comparison + variant (`results.py:85-95`), so concurrent runs overwrite each other. | The job id dedupes; temp file + atomic rename; a digest in the file name.                                   |
| H2  | Training re-reads the path instead of using the captured bytes (`results.py:240,285`).                             | Train from the bytes.                                                                                       |
| H3  | Plain `str` paths (`classification.py:82`, `regression.py:71,155,158`).                                            | `WorkspacePath`.                                                                                            |
| H4  | The inference fallback may pick a `__refit_` file (`classification.py:830-837`, `regression.py:646-654`).          | An exact match, or an error that lists the candidates.                                                      |
| H5  | `os.chdir` (`classification.py:537,548`; `regression.py:414-434`).                                                 | Absolute paths.                                                                                             |
| H6  | Global logger changes and leaked handlers (`classification.py:28-53`, `regression.py:29-41`, `features.py:72-87`). | A per-job handler in `child.py`.                                                                            |
| H7  | PyCaret `verbose=True`.                                                                                            | Obsolete once PyCaret is removed (Phase 7.0): the engine never prints and logs through the per-job handler. |
| H8  | The regressor lacks the checks, progress, fold clamp and replay `chart_data`.                                      | Same as the classifier.                                                                                     |
| H9  | A shared params YAML (`features.py:513`).                                                                          | Per job.                                                                                                    |
| H10 | A new process pool per mask (`features.py:846-850`).                                                               | One pool per job; a real `n_workers`.                                                                       |
| H11 | A CUDA probe just to set a flag (`features.py:697-712`).                                                           | Remove it.                                                                                                  |
| H12 | The analysis reads only rows created by the run (`repositories/radiomics.py:261`).                                 | Link reused rows to the current run.                                                                        |
| H13 | `write_agent_output` deletes the run's temp dirs while jobs are running (`agent_output.py:25-35,213`).             | Per-job dirs; skip cleanup while jobs are active.                                                           |
| H14 | A shared plots dir; the figure key is not content-addressed (`features.py:1538,1589,1599`).                        | A per-job dir; a digest key.                                                                                |
| H15 | `mutual_info` has no `random_state`.                                                                               | `split_seed`.                                                                                               |
| H16 | RFE keeps all features (`features.py:1319,1647`).                                                                  | `n_features_to_select=top_features`.                                                                        |
| H17 | `set_key=top_{n}` ignores the method → 409.                                                                        | Add a config digest.                                                                                        |
| H18 | Code outside `try` raises (`features.py:308,325,344,365,376-377,1084-1103`).                                       | Validate in the input model; the child always writes a result.                                              |
| H19 | Progress arrives in bursts.                                                                                        | A sync progress file relayed by the supervisor.                                                             |
| H20 | Writes are refused unless the run is `running` (`repositories/radiomics.py:59-78`).                                | The supervisor checks before it starts.                                                                     |

### D. Light tools (Plan 2)

| ID  | Tool                                            | Problem                                                                                                 | Fix                                                                   |
| --- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| L1  | 14 DB tools                                     | `raise_for_status()` raises; default 5 s timeout.                                                       | `error_result`; the shared client.                                    |
| L2  | `get_all_patients_info`, `get_all_studies_info` | They raise, and dump whole tables.                                                                      | `error_result`; `limit` / `skip`.                                     |
| L3  | `get_invalidate_segmentations`                  | No filter.                                                                                              | Call `/segmentations/unvalidated/`.                                   |
| L4  | `check_having_mask`                             | A 500 looks like "no mask"; duplicate logic.                                                            | `[]` only on 404; one implementation.                                 |
| L5  | `validate_population_anatomy`                   | Matches empty or short text; raises.                                                                    | A minimum length; `error_result`.                                     |
| L6  | `extract_cohort_labels`                         | Sync `json.load`; `print()`; one LLM error drops every label.                                           | `to_thread`; the logger; keep partial results.                        |
| L7  | `generate_targets`                              | A global flag; raises.                                                                                  | Per-session state; `error_result`.                                    |
| L8  | `run_segmentation`                              | `float()` of an env var at import; a fixed temp name; tracebacks sent to the LLM; the wrong error flag. | Lazy validated settings; `mkstemp`; log tracebacks; the correct flag. |
| L9  | `browse_workspace`                              | The workspace path is not resolved.                                                                     | `resolve()`.                                                          |
| L10 | `get_modeling_results`                          | Returns plain `"Error: ..."`.                                                                           | `error_result`.                                                       |
| L11 | `list_models`                                   | Returns a dict in one case and a JSON string in another.                                                | One type.                                                             |
| L12 | `write_agent_output`                            | Sync `rmtree`.                                                                                          | `to_thread` (+ H13).                                                  |
| L13 | `run_segmentation_with_guard`                   | Two names.                                                                                              | One name; `critical=True`.                                            |
| L14 | scratchpad prompts                              | Wrong argument names; the wrong agent id.                                                               | Fix the prompts in `agent.md`; register/status are harness-only.      |

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

---

## Appendix P — Persistence Design (revision 3.2)

This appendix is the full text of `outputs/persistence-design/cubeai-persistence-design.md`, merged here so the roadmap is one plan. Only the headings changed: they are one level deeper, and the sections are numbered P.1–P.12, so `§P.4` means section 4 of this appendix. Plan 6 implements it, and every other plan uses its names and transactions. The standalone file is kept with the same text; a change to one is made to both.


Date: 1 October 2026 · Branch `Refactor-Version` @ `0c4503e6` · Alembic head `n1f0a2c3d4e5` · Revision 3.1 (after the architecture ↔ database review and its follow-up) · Revision 3.2: 2 October 2026 (LangGraph-native agent nodes)

**Scope:** the PostgreSQL persistence owned by the Application API for the CUBE.AI Agent Harness. That covers users, sessions, chats, the session graph, graph changes and approvals, accepted runs and execution attempts, events, and how all of these connect to the typed research tables. Paths are relative to the repository root.

**What the design supports:**
- A **Main Agent** decides what should happen. A **Session Graph** holds the submitted work. An **Executor** is the only component that runs long work.
- **Auto** mode (the default: submitted work starts immediately) and **Plan** mode (submitted work waits for user approval).
- **PHI-Local** privacy.
- **Agent** and **Tool** nodes.
- Live edits and reruns. A session never "finishes"; it only becomes idle.

**What changed in revision 3:**
- `execution_mode` defaults to `auto`.
- New nodes get their `node_id` in the proposal, so a change's content names every node it defines (D7).
- A fixed input-resolution rule, pinned per port (D23).
- Fencing per attempt, enforced in the database (D24).
- Selections freeze when the run completes (D25).
- One non-terminal attempt per session, and an attempt becomes terminal only on evidence that its work stopped (D9).
- `graph_nodes.hold_reason` is removed. Every approval reason is computed when a change is proposed, and an expectation hold lives on the run that caused it.
- `pausing` is derived. The run's policy freezes `privacy_mode`. Control-tool results are written atomically with the change. `call_id` is unique per turn.
- The migration is two cut-overs with no dual path (D26).

**What changed in revision 3.1:**
- Input resolution is strict. One compatible candidate is bound; none is `MISSING_INPUT`; more than one is `AMBIGUOUS_INPUT` unless the node names its source in `input_sources` (D23). Nothing is guessed.
- Every write path is named, and the shared trigger rejects any write that does not declare one (D24). A user's decision in an in-node gate is its own path.
- The pinned upstream run is defined exactly: **the current completed run of the selected live upstream node at resolution time**.
- "One non-terminal attempt per session" is marked as a constraint of the current, sequential phase, not an architecture invariant (§P.4, *Current-phase constraints*).
- The migration semantics of legacy rows are explicit: a draft never becomes an applied change, never-started runs are closed as migration cleanup (not as a user cancellation), and outputs are backfilled only from an exact mapping.

**What changed in revision 3.2 (LangGraph-native agent nodes, D27):**
- Agent-node reasoning state is stored: `agent_checkpoints` and `agent_checkpoint_writes`, written through the Application API by the run's current attempt (D27). They are not provenance and are purged after a retention period.
- A new non-terminal attempt state, `suspended`: an in-node gate or a pause holds no process, survives a restart, and is resumed by the **same** attempt (D9, T10). The derived activity gains `awaiting_user`.
- A new transaction, **T10** (suspend, gate decision, resume). T4 rule (f) reads the checkpoint's `pending_tools` for a resumable run; T4 may copy a `seed_from` checkpoint into a new run; T5 leaves `suspended` attempts alone; T7 confirms a `suspended` attempt at once.
- D19 no longer lists in-run approvals and agent reasoning as unstored; §P.10 drops the two rows that waited for "a durable agent checkpointer".

---

### P.1 Design decisions and their reasons

| # | Decision | Reason |
|---|---|---|
| **D1** | **The Application API is the only owner of the database.** The Agent System and the execution backends read and write through its HTTP API. | One writer per table means no competing writers and one place to enforce every invariant. The rule is already enforced by tests. |
| **D2** | **A very simple `users` table.** Every session has exactly one owner (`sessions.user_id NOT NULL`), and sessions are not shared. There are no roles, teams, sharing tables or credentials. | Sessions hold private research, so each one needs an owner to authorize against. Everything else a session owns inherits the owner through `session_id`, so only one column carries ownership. Authentication is a separate mechanism that maps a verified login to `users.username`. |
| **D3** | **One chat per session.** `chats` keeps its table, with `UNIQUE (session_id)`. The chat is created with the session (T0), and every message, Main reply, proposal and approval of the session goes to it. Conversation context and compaction are per session, like the inbox cursor. A new topic or a different study is a new session (D22). | The session already has one live graph, one serialized Main and one inbox, so a second conversation over the same graph would change it without seeing what was said in the first. One chat also removes the need to route replies, approvals and events to a particular chat. Today's frontend already uses only the first chat of each session (`resolveChatId` in `ChatSidebar.tsx`). Keeping the table, rather than moving `session_id` onto `chat_messages`, changes no route or frontend code, and dropping the constraint later is all it takes to allow many chats. |
| **D4** | **One live graph per session, stored as `session_graphs`** (the existing 1:1 `plans` row, renamed). It gains a `version` used for compare-and-swap. | The header already exists, is already locked in the established order (session → graph), and is already referenced by triggers. A version on it makes every graph change atomic and detects stale proposals. |
| **D5** | **Execution is sequential only.** The node order is `graph_nodes.position` (`Node 1 → Node 2 → Node 3`). There is **no edge table**. Each node keeps a stable `node_id` (today's `step_id`, renamed). | Branching, parallel execution and joins are not part of the current system. An edge table would store the same order a second time and need its own validation. A stable `node_id`, port-level `resolved_inputs` (D23) and a session-wide active-attempt index (D9) let the design grow later without a rewrite (§P.5, *No `node_connections` table*). |
| **D6** | **Nodes are `graph_nodes`** (today's `plan_steps`, renamed and extended). Each node has a target described by `target_kind` (agent or tool), `target_name` and `target_version`, plus `params`, `expectations` and a `removed_at` tombstone. A node's definition changes only through an applied graph change, and its `node_id` never changes. Each node also gets a short per-session handle, `node_seq`, for the Main and the UI. | The Main and the user refer to the same node across edits and reruns. Keeping the existing rows keeps their FKs, triggers and composite keys working. A UUID is long and easy for a model to copy wrongly, so the model sees `n3` and the harness maps it to the `node_id`. |
| **D7** | **Every graph mutation is a `graph_changes` row.** That row is the only durable approval mechanism. It covers Plan-mode work, mandatory approvals in Auto mode, and Main direct actions promoted to nodes. **Every approval reason is computed when the change is proposed**: from the session's mode, from the target manifests, and from the runs the change would supersede. Nothing waits for approval when a node starts. The proposer mints the `node_id` of every new node, so the content names every node it defines. There is at most one pending change per session. A change that needs approval is applied only by a **user** decision that quotes its exact `content_digest`. | Unapproved work must survive restart and stay unapproved. An approval must bind to exact, immutable content. Runs must be able to find their definition inside the change that admitted them, so the content cannot use placeholders. Once nodes are editable, this row is the only record of what was accepted, by whom and when. Because the reasons are known at proposal time, a harness check can never replace a proposal the user is reviewing. |
| **D8** | **Runs are created lazily, when a node starts.** `node_runs` (today's `agent_runs`, renamed because runs now execute tool nodes too) gets a row only when the Executor actually starts a node, together with its first attempt. Approving or applying a change creates **no** runs: the applied node itself is the accepted work. A node can have many runs over time, and at most one is *current*. An intentional rerun or an invalidation supersedes the current run, and the next start creates a new one. Old runs stay as history. | `node_runs` is then a true execution history, with no pending rows for work that may never run. Edits to not-yet-started nodes cancel nothing, and acceptance is stored once (on the node and its change), not three times. Every typed result row is keyed by its run (`created_by_run_id`), so the run stays the anchor of results. |
| **D9** | **Execution attempts are a table, `run_attempts`.** One attempt is one real execution try with one submission key, persisted **before** dispatch. A controller restart, a reconnect, or resubmitting an uncertain submission **reuses** the existing attempt. A new attempt is created only when execution must truly start again. **Within a session, at most one attempt is non-terminal at any time.** This is a current-phase constraint that follows from sequential execution, not an architecture invariant (§P.4); the invariant in every phase is at most one non-terminal attempt **per run**. `unknown` and `suspended` count as non-terminal. **An attempt becomes terminal only on evidence that its work stopped.** A timeout is a cancel request, not a failure, until the stop is confirmed. A **suspended** attempt (an agent node waiting on a gate or a pause, D27) has no running work, so it is resumed by the same attempt, and cancelling it is confirmed at once. | Each try needs its own submission identity, uncertainty state, cancellation intent and error history. The Watchdog and the Recovery Agent read that history. Execution is sequential, so a superseded run that is still stopping must block the next start instead of running beside it and doubling GPU work. If an attempt were closed while its process still ran, the next attempt could start next to a live writer. |
| **D10** | **Segmentation fan-out stays inside one node attempt.** The Segmentation node's attempt is the execution the harness tracks. Its per-(target, volume) MONAI jobs are domain work items on `segmentation_pairs`. Each pair's `inference_job_id` is `sha256(batch_correlation_id\|pair_id\|model\|prompt)`. The `batch_correlation_id` is minted once per batch and stored in `segmentation_batches.config`, and the batch row is keyed by the run (`created_by_run_id`, `batch_key`). | Execution is sequential, so hundreds of pair jobs cannot be graph nodes. Any attempt of the same run finds the same batch row, therefore the same correlation id, and therefore the same pair job ids, so it re-attaches finished and live jobs instead of duplicating GPU work. A new run creates a new batch and new jobs, which is the intended rerun behaviour. |
| **D11** | **Results live in the typed domain tables.** A run's inputs are pinned per port in `resolved_inputs` at first start (D23). Its port → result selection (the architecture's *artifacts*) is frozen in `node_runs.outputs` at completion. There is no generic artifact-binding table. | The typed tables already carry lineage with composite FKs and producer triggers. A generic binding table would be a weaker second copy that could disagree with them. |
| **D12** | **Backends own physical execution state:** Celery/Redis records, the MONAI job store, GPU capacity. SQL stores identity and the last *confirmed* outcome. There are no resource-grant or controller-lease tables. | Two owners of the same state drift apart. The one-active-attempt constraint plus attempt fencing already stop duplicate starts and stale writers. |
| **D13** | **`sessions.control_state` holds only control intent:** `active`, `paused`, `archived`. The activity (`idle`, `running`, `pausing`, `awaiting_approval`, `blocked`) is **derived** and never stored. `pausing` means: paused, with an attempt still non-terminal. | A session never permanently finishes; new work can arrive later. User intent must not be overwritten by a progress projection, and derived progress cannot drift. `pausing` is progress, not intent, so storing it would need a second writer. |
| **D14** | **Accepting user input is one atomic operation.** The Application API stores the chat message and a durable `user_input` event in one transaction. The frontend sends the message to the Agent System, which calls the Application API and enqueues the message only after that commit. The inbox advances `sessions.inbox_handled_through_event_id` only after handling. | Acknowledged input must never be lost between "saved" and "processed", and it must be re-queued after a restart. When the Agent System makes the call, it learns about the input synchronously, so a lost notification cannot leave a message unhandled until the next restart. |
| **D15** | **Session settings are durable:** `execution_mode` (**default `auto`**, decided 1 October 2026), `privacy_mode`, `tokens_used`. Plan mode is opt-in. The mandatory approval reasons apply in both modes. | If a restart silently switched Auto/Plan, weakened PHI protection or reset a budget, that would be a correctness and privacy failure. |
| **D16** | **Each run freezes its execution policy** (`max_attempts`, `timeout_s`, `idempotent`, `fresh`, `privacy_mode`) when the run is created (at node start). Every write from execution presents its `attempt_id` (**mandatory fencing**, D24). | A deployment must not change limits that were already accepted. A run that started under `standard` must not keep calling external models after the session moved to `phi_local`, so the switch cancels such attempts (T7). A stale attempt must never publish. |
| **D17** | **Standalone nnUNet training has its own receipt table, `training_jobs`,** attached to `workflow`. Training runs outside the session graph. | The training job id must be stored before submission. Otherwise a lost response leads to a second full training, and the job can never be cancelled or unfrozen safely. |
| **D18** | **`render` stored files are never scientific inputs.** Only `retained` stored files (immutable bytes) or typed rows can be pinned. | A render's digest can change under the same id (`repositories/artifacts.py:192-203`). |
| **D19** | **No execution records** (no run, no attempt) for simple direct tool calls; their results are saved in the conversation (D21). **Not persisted at all:** the in-memory inbox queue, cache indexes, resource pools, heartbeats, Main turns as objects (and any Main, Composer or Recovery reasoning state), unsubmitted Composer drafts, and start-time refusals for budget or resources. The Executor evaluates those refusals again before every start and publishes them as `node_start_refused` events. **Since revision 3.2**, in-run approvals inside agent nodes and agent-node reasoning state **are** persisted, as gates on suspended attempts and as agent checkpoints (D27). | These are runtime or derivable. Durable approvals of work are graph changes (D7); an approval inside a running node is a gate (D27), because its node can now resume from a checkpoint. The Main is rebuilt from the chat (D21), so it needs no checkpoint. A missing GPU or an exhausted budget is a condition, not a decision, so it has nothing to approve or store. |
| **D20** | **One vocabulary, one meaning per word (§P.2).** The schema is renamed to it: `plans` → `session_graphs`, `plan_steps` → `graph_nodes`, `agent_runs` → `node_runs`, `ws_log` → `session_events`, `artifacts` → `stored_files`, `segmentation_runs` → `segmentation_batches`, `segmentations` → `segmentation_masks`, `models` → `inference_models`, together with their columns. `workflow` and `workflow_details` keep their names. | Today "run", "plan", "artifact" and "model" each mean two to four different things. Names inherited from a fixed, agent-only pipeline would mislead every reader of a graph that holds agent **and** tool nodes. `workflow` keeps its name but gets one meaning: an nnUNet training task. Work in a session is a *session graph*, never a workflow. |
| **D21** | **The Main loop has no table of its own.** Its replies, and the calls and results of its tools, are saved in the session's `chat_messages` as ordered rows: an assistant row with `metadata.kind = 'tool_calls'` listing one or more calls by `call_id`, one `role='tool'` row per result with the same `call_id` (with an optional `ui` hint the frontend renders), then the answer. Rows of one turn share `turn_id` and are ordered by `seq`. Large results are saved as `retained` `stored_files` with no run, and the message references the `file_id`. Anything that writes research results, waits on a human gate or runs long (for example the Population agent creating a cohort) is submitted with `execute` as **one graph node** in the session's research line (D22). There is no Planner and no plan, and in Auto mode it starts at once. **The result of a control tool (`execute`, `graph_apply`) is written by the Application API in the same transaction as the graph change** (T1/T2). | Read-style answers are conversation, so they belong with the conversation and survive refresh and restart. Research results must have a producing run: `cohorts.created_by_run_id` is NOT NULL and the producer trigger checks it. Long or human-gated work must survive restart, and in Plan mode it must wait for approval. A direct call has none of these, and a one-node `execute` has all of them at the cost of a single call. If a control tool's result were written after the change, a crash in between would mark a call that succeeded as interrupted, and the Main would submit the same work twice. |
| **D22** | **One session = one research line.** The session's graph is one pipeline. For every request the Main decides, and records explicitly in the change's ops, which of four things it is: (1) **a question** → a direct read tool, nothing in the graph; (2) **a modification of existing work** ("add patients", "change the criteria", "exclude …") → `UpdateParams`/`RerunNode` on the existing node, which supersedes it and every node after it; (3) **the next step of this line** → `AddNode` at the end of the sequence; (4) **a different study** → the Main proposes a **new session** and creates it only after the user accepts. It is never appended to the current sequence. Several cohorts of the *same* study (for example train and test) are one Population node producing several `cohort_key`s. When it is unclear whether a request modifies or starts something new, the Main asks. Any change that would supersede a **completed** run carries the mandatory approval reason `supersedes_completed`, even in Auto mode. | The graph is sequential, so an unrelated population appended after segmentation would wait for it and become the upstream of later nodes it has nothing to do with. A modification silently reruns expensive downstream work unless the user sees and approves it. The modify-or-new judgement is strategy, so it belongs to the Main (a skill), not to a harness rule. The approval reason is a boundary, so the harness enforces it. |
| **D23** | **Inputs are resolved strictly, then pinned per port.** For each input port the Validator collects every **candidate**: an output port, with a compatible type, of a live node earlier in the sequence. **Exactly one candidate** → it is bound. **None** → `MISSING_INPUT` for a required port; an optional port stays empty. **More than one** → `AMBIGUOUS_INPUT`, unless the node's `input_sources` names the source for that port (`{port: {node_id, port}}`), which must be one of the candidates. The rule never picks by distance or by type closeness. The Validator runs it on the whole target sequence for every change, so an insert that makes a later node ambiguous is refused until that node names its source. T4 pins the result per port in `resolved_inputs`. **The pinned upstream run must be the current completed run of the selected live upstream node at resolution time**: the run with `superseded_at IS NULL` and `status = 'completed'` of that node, read under the session lock in T4. | Without edges, this rule is the only thing that says where an input comes from. Inputs are not always adjacent: feature extraction reads the cohort from Node 1 and the masks from Node 2. A silent "nearest wins" rule would bind the wrong result as soon as two nodes produce the same type (for example a training node and a refit node both producing `ModelingOutput`), and a node with two compatible ports would have no answer at all. An explicit error plus an explicit source keeps every binding decided by the Main or the user, never by the harness. `input_sources` is exactly a DAG edge, so a DAG later only makes it mandatory. |
| **D24** | **Fencing is enforced by the database, on every write path.** The shared trigger sits on every table that execution writes (each domain table with `created_by_run_id`, `segmentation_pairs`, `stored_files`, `node_runs`, `run_attempts`), so no route can bypass it. Every write must declare its path in a transaction setting, and a write that declares none is rejected: **(1) attempt** (`cubeai.attempt_id`): the trigger locks that attempt row `FOR SHARE` and requires it to belong to the row's run and to be its non-terminal attempt; **(2) internal job** (`cubeai.run_id`): jobs that outlive one attempt (segmentation pairs, radiomics jobs keyed by the run), limited to run-scoped natural-key inserts and pair bookkeeping, on a run that is not completed; **(3) user gate** (`cubeai.actor = 'user'`): the owner's decision in an in-node gate (cohort approval, segmentation review), limited to the gate columns, on a run that is `running` and current; **(4) harness** (`cubeai.actor = 'harness'`): T2, T5, T7 and T6's run projection, limited to `node_runs`/`run_attempts` lifecycle columns, under the session lock; **(5) migration** (`cubeai.actor = 'migration'`), set only inside Alembic. A run-less `stored_files` row (a direct Main result, D21) needs no fence, because it belongs to no run. | Domain rows carry only `created_by_run_id`, so a check "the run has some active attempt" would accept writes from an old attempt that is still alive. Without the row lock, a write can pass the check while T6 or T7 closes the attempt, and both commit (the current `model_results` trigger already uses `FOR SHARE` for this reason: `n1f0a2c3d4e5_v1_n_model_results_trigger_status_fix.py:65`). A route-level rule would protect only the routes that remember it; a table-level rule with a declared path protects every write, including ones added later. |
| **D25** | **Selections freeze with the run.** Selection and approval columns on domain rows (`model_results.selected`, `segmentation_pairs.selected_segmentation_uid`/`review_decision`, `segmentation_batches.review_status`, `cohorts.frozen_at`, `feature_sets.frozen_at`) can change only while their run is `running`, current and fenced. At completion, `node_runs.outputs` becomes the only port → result selection. A different choice after that is a `RerunNode`. | If a selection could change after completion, a downstream run would have pinned a choice that no longer exists while still counting as current. That gives two sources of truth and breaks reproducibility without any graph change. |
| **D26** | **Two cut-overs, no dual path.** The renames ship with the mechanical code rename in **one commit**. The additive migration ships with the harness persistence code. The AutoGen path is not kept working against the new schema. The rows it wrote are history: never-started legacy runs are closed, and running ones are recorded as interrupted. | Renames, value rewrites and narrowed CHECKs break the old code the moment they apply, so "the old path keeps working" cannot be true. Keeping AutoGen alive would need legacy `pending` runs, unfenced attempts and a "never both in one session" rule, all for code the roadmap already freezes and deletes. |
| **D27** | **Agent checkpoints and suspended attempts (revision 3.2).** An agent node with `resumable` set stores its LangGraph checkpoints in `agent_checkpoints` / `agent_checkpoint_writes`, through the Application API (`HttpCheckpointSaver`); no LangGraph saver ever connects to PostgreSQL. **The thread is the run** (`thread_id = run_id`): every attempt of a run continues the same thread, and a rerun is a new run with a new, empty thread, unless its change sets `seed_from = {run_id, checkpoint_id}` (an earlier run of the same node), in which case T4 copies that one checkpoint into the new thread when its `spec_digest` and `privacy_mode` match, and starts fresh otherwise (`seed_from.applied = false`). **Only the run's current non-terminal attempt writes** (the D24 attempt path); any attempt of the run reads. A checkpoint is **reasoning state, never a result**: results stay in typed rows and `outputs`, and nothing pins or selects a checkpoint. **Gates and pause suspend the attempt** (`state = 'suspended'`, T10) instead of holding a process; the gate decision is written on the `user gate` path with the domain gate columns, and the same attempt resumes. Checkpoints are purged `AGENT_CHECKPOINT_RETENTION_DAYS` (default 14) after their run is terminal, unless a live `seed_from` names them; this is the one deliberate exception to "provenance is never hard-deleted" (I16), because a checkpoint is not provenance. The Main, the Composer and the Recovery Agent are never checkpointed (D21). | Agent nodes run for hours and wait on people. Without a checkpoint, a restart makes the agent reason again and an approval inside a node is lost, and a gate holds an in-memory task for as long as the user takes. Keying the thread by the run reuses the existing identity rules: a technical restart continues (I8), an intentional rerun starts clean. Writing through the Application API keeps D1, and fencing by attempt keeps a superseded attempt from writing state that a later attempt would resume. Keeping checkpoints out of results keeps one source of truth for science (D11). |

**Resulting schema change:**
- 6 new tables: `users`, `graph_changes`, `run_attempts`, `training_jobs`, `agent_checkpoints`, `agent_checkpoint_writes` (the last two in revision 3.2, D27).
- 8 tables renamed (D20), plus column renames in 6 kept tables, together with their constraints and trigger functions.
- About 25 columns added to `sessions`, `session_graphs`, `graph_nodes`, `node_runs` and `chat_messages`.
- No table is removed.
- Every typed domain table keeps its data. Only the producer/fencing/selection trigger and some FK column names change (`created_by_run_id`, `segmentation_batch_id`, `file_id`, `inference_model_id`).

---

### P.2 Vocabulary and names

Each word has exactly one meaning in the schema, the code and the API.

| Term | Meaning | Persisted as |
|---|---|---|
| **user** | The owner of sessions | `users` |
| **session** | The owned root scope: one research line, one live graph, one chat | `sessions` |
| **chat / message** | The one conversation of a session / one turn in it | `chats` / `chat_messages` |
| **session event** | A durable, replayable notification in a session | `session_events` |
| **session graph** | The one live graph of a session (header + version) | `session_graphs` |
| **graph change** | A submitted, immutable mutation of the graph, including its approval | `graph_changes` |
| **graph node** | A stable unit of work in the graph, with a target and a position. `node_seq` is its short handle (`n3`) | `graph_nodes` |
| **target** | The registry entry a node invokes: an agent or a tool | `graph_nodes.target_kind/target_name/target_version` |
| **run** | One execution of a node's accepted definition, created when the node starts. **Only this** | `node_runs` |
| **attempt** | One execution try of a run | `run_attempts` |
| **suspension** | An attempt waiting with no running work: on an in-node **gate** (a user decision) or on a **pause**. Resumed by the same attempt | `run_attempts.state = 'suspended'`, `run_attempts.suspension` |
| **agent checkpoint** | A LangGraph snapshot of an agent node's reasoning state, in the run's thread (`thread_id = run_id`). Never a result | `agent_checkpoints`, `agent_checkpoint_writes` |
| **current run** | The run of a node with `superseded_at IS NULL`. A *current completed run* also has `status = 'completed'` | `node_runs` (partial unique index) |
| **input binding** | Where an input port of a run comes from: the only compatible output of an earlier live node, or the source the node names in `input_sources` when there are several (D23). Pinned per port at first start, to the current completed run of that node at resolution time | `graph_nodes.input_sources`, `node_runs.resolved_inputs` |
| **output** | A run's typed port output (the architecture's *artifact*) | `node_runs.outputs` |
| **hold** | An expectation violated by a completed run, which stops the next node until a change releases it | `node_runs.downstream_hold`, `hold_released_by_change_id` |
| **stored file** | File bytes (figure, CSV) or a render path. `retention_class` says how a file is kept, not who made it: `retained` = durable, immutable bytes; `render` = a regenerable preview | `stored_files` |
| **job** | A backend execution identity | `run_attempts.submission_key`, `segmentation_pairs.inference_job_id`, `training_jobs` |
| **batch** | One segmentation batch over a cohort | `segmentation_batches` |
| **series** | A DICOM image series mirrored from Orthanc | `volume_series` |
| **mask** | A segmentation result: labelled regions over a volume, stored in Orthanc as a DICOM SEG series (key `label_series_instance_uid`) | `segmentation_masks` |
| **inference model** | A MONAI segmentation model in the catalogue | `inference_models` |
| **workflow** | An nnUNet training task: a dataset with frozen case membership. This is its only meaning; work in a session is a *session graph*, never a workflow | `workflow`, `workflow_details` |
| **plan** | **Only** Plan mode and the Planner/Composer. It is never a table name | — |

**Conventions:**
- tables are plural snake_case nouns;
- primary keys are `<entity>_id`, and foreign keys repeat the referenced name, prefixed by role when needed (`created_by_run_id`, `predictions_file_id`);
- timestamps end in `*_at` and digests in `*_digest`;
- `status` is an entity outcome and `state` an attempt's observed state. Control intent is `control_state`, never `status`;
- every JSONB column with a contract carries its own version key `v`;
- model, schema, repository and route files share the table name;
- constraint, index and trigger names follow `db/base.py` `NAMING_CONVENTION`.

#### Rename map (today's name → name used in this design)

| Table | Columns renamed with it |
|---|---|
| `plans` → **`session_graphs`** | — |
| `plan_steps` → **`graph_nodes`** | `step_id` → `node_id`, `step_order` → `position`, `agent_type` → `target_name`, `instructions` → `params` |
| `agent_runs` → **`node_runs`** | `agent_run_id` → `run_id`, `step_id` → `node_id`, `input_refs` → `resolved_inputs`, `durable_notes` → `scratchpad_notes`, `warnings` → `scratchpad_warnings`, `output_summary` → `scratchpad_summary` |
| `ws_log` → **`session_events`** | `msg_type` → `event_type` |
| `artifacts` → **`stored_files`** | `artifact_id` → `file_id`; FKs `model_results.predictions_artifact_id`/`input_artifact_id` → `predictions_file_id`/`input_file_id`, `report_images.artifact_id` → `file_id`; `retention_class` values `user_retained`/`system_render` → `retained`/`render` |
| `segmentation_runs` → **`segmentation_batches`** | `segmentation_run_id` → `segmentation_batch_id` (also in targets and pairs) |
| `segmentations` → **`segmentation_masks`** | `model_id` → `inference_model_id` |
| `models` → **`inference_models`** | `segmentation_targets.model_id` → `inference_model_id` |
| `sessions` (kept) | `status` → `control_state` |
| `chats` (kept) | `last_activity` → `last_activity_at` |
| `chat_messages` (kept) | `timestamp` → `created_at`, `extra_data` → `metadata` (ORM attribute `metadata_`) |
| `segmentation_pairs` (kept) | `external_job_id` → `inference_job_id` |
| `radiomics_features` (kept) | `id` → `radiomics_feature_id` |
| `workflow`, `workflow_details` (kept) | — |
| every domain table | `created_by_agent_run_id` → `created_by_run_id` |

**Related names:**

| Area | Today → design |
|---|---|
| Event type | `pipeline_agent_status` → `node_status` |
| WebSocket envelope | The id fields become `node_id`, `run_id`, `attempt_id` (version 2). They are always null today |
| Segmentation code | The local `run_id` (uuid) → `batch_correlation_id`, also the key in `segmentation_batches.config` |
| Routes | `/plans` → `/session-graphs`, `/steps` → `/graph-nodes`, `/agent-runs` → `/runs`, `/ws_logs` → `/session-events`, `/artifacts` → `/files`, `/segmentation-runs` → `/segmentation-batches`, `/segmentations` → `/segmentation-masks`, `/models` → `/inference-models`, `/chat_messages` → `/chat-messages`; `/chat_sessions` is removed; `/workflow` keeps its name |
| ORM classes | `SessionGraph`, `GraphNode`, `NodeRun`, `SessionEvent`, `StoredFile`, `SegmentationBatch`, `SegmentationMask`, `InferenceModel`; `Workflow` and `WorkflowDetail` keep their names |
| Execution context | `ToolContext.agent_run_id/step_id/graph_version` → `run_id`, `attempt_id`, `node_id`. `RunAccepted` returns `{node_id, change_id, status}`; it has no `run_id`, because the run is created only when the node starts |

`execution_mode`, `run_attempts`, `training_jobs`, `outputs`, `compiled_prompt` and every other domain name keep their names, because they already say what they store.

---

### P.3 Starting point: the current system (verified in code)

This section describes today's code, so it uses today's names. Every other section uses the names from §P.2.

#### P.3.1 Tables

| Group | Tables | Role |
|---|---|---|
| Session spine | `sessions`, `chats` (N per session), `chat_messages`, `ws_log` | Identity and lifecycle, conversations, durable event log and replay cursor |
| Plan & execution | `plans` (1:1 session), `plan_steps`, `agent_runs` (`UNIQUE(step_id)`) | The approved linear plan, and one execution per step |
| Stored bytes | `artifacts` | `user_retained` (immutable bytes) or `system_render` (regenerable preview) |
| Typed results | `cohorts`, `cohort_members`, `segmentation_runs`, `segmentation_targets`, `segmentation_pairs`, `radiomics_features`, `feature_sets`, `model_results`, `reports`, `report_images`, `literature_reviews`, `literature_papers` | Every scientific output. Each row has `created_by_agent_run_id` and a natural key scoped to the run |
| Clinical mirror | `patients`, `studies`, `volume_series`, `segmentations` | Orthanc metadata, `archive_status` instead of deletes |
| Training | `models`, `workflow`, `workflow_details` | Model catalogue, nnUNet tasks, frozen membership |

There is no `users` table and no authentication, and the session listing is global.

#### P.3.2 Invariants the database already enforces (all kept)

- **Sessions and plans:**
  - `sessions.prompt` is immutable;
  - every session has exactly one plan;
  - `plans.approved_at` is one-way.
- **Run cardinality:** a draft plan has 0 runs, and an approved plan has exactly one run per step (`plans_enforce_run_cardinality`, `f1f0…:23`). *This one is replaced, not kept: runs become lazy (D8).*
- **Run row rules** (`agent_runs_enforce_row_rules`, `f1f0…:68`):
  - runs are never deleted and their identity is immutable;
  - the prompt, `input_refs` and `started_at` freeze at start;
  - a completed run is immutable.
- **Producer** (`domain_rows_enforce_producer`, `i1f0…:51`): a domain row's run must execute an allowed `plan_steps.agent_type`. The `model_results` trigger locks its run row with `FOR SHARE` (`n1f0…:65`).
- **Domain rows:**
  - composite FKs keep cohort alignment;
  - `segmentation_pairs.external_job_id` is never re-pointed (`h1f0…:278`);
  - `review_request_hash` binds a segmentation review to its content;
  - `ON DELETE RESTRICT` everywhere.

#### P.3.3 Flows the design builds on

1. **Approval.** `approve_plan` locks session → plan, validates the agent types, sets `approved_at` and creates one pending run per step, all in one transaction (`repositories/plan.py:116`). Today a changed plan requires a new session (`plan.py:66`).
2. **Execution.** A linear pipeline (`execution_team.py:168`). Each step starts (freezing the prompt and `input_refs`; `run_context.py:158` pins the completed upstream runs), writes typed rows keyed by its run, and then completes or fails. Every transition goes through `publication()` (`services/publication.py:69`), which commits the state change and its `ws_log` event together under the session lock.
3. **Retry.** A failed run returns to `running` on the same row. Attempts are not counted and writers are not fenced (`repositories/agent_runs.py:130-172`).
4. **Idempotent partial recovery.** Natural keys are scoped to the run, so a retry finds its own partial results again.
5. **Segmentation.**
   - Each pair has its own MONAI job, `id = sha256(run_id|pair_id|model|prompt)` (`segmentation/module.py:1436-1441`), and a resumable checkpoint.
   - The MONAI job store is file-backed and marks interrupted jobs at startup (`monailabel/endpoints/inference_jobs.py:31, 63`).
   - A terminal-failed job **cannot** currently be re-queued under the same id: the terminal guard rejects it (`monailabel/services/job_root.py:135-143`, `inference_jobs.py:264-290`).
6. **Training.** `workflow.py:516` mints a job id and POSTs it to MONAI. MONAI dedupes by id and input hash, but the id is never stored in SQL.
7. **Chat input.** The frontend saves the message (`ChatSidebar.tsx:484`) and then makes a second call to the Agent System's `/input`, which queues it in memory (`agent_system/api/routes/sessions.py:162`, `socket_store.py:187-212`). A failure between or after those two calls loses the obligation to process the message.
8. **Restart.** A `running` run hydrates as paused, and no replacement task is created (`session_store.py:149`).
9. **Session status.** The Agent System writes it in a separate transaction from the runs.

---

### P.4 Invariants and state ownership

| # | Invariant | Mechanism |
|---|---|---|
| I1 | Every session has exactly one owner. Chats, graph, runs, attempts, events and results inherit it through `session_id`. Nothing is shared. | `sessions.user_id NOT NULL`; an ownership check in every session-scoped route |
| I2 | A session has one live graph, possibly empty, versioned by `session_graphs.version`. | 1:1 `session_graphs` + CAS |
| I3 | Execution order is the live sequence of `graph_nodes` by `position`. There are no branches, parallel nodes or joins. | Apply-time validation of one contiguous live sequence; sequential Executor |
| I4 | A node's `node_id` never changes. Edits and reorders keep it, removal tombstones it, and only new work gets a new id, minted in the proposal that adds it. | Row trigger; T1/T2 check that every new id is unused |
| I5 | Nodes exist only because an **applied** change created them. A run exists only because the Executor started a live node, and it carries the change that accepted that node's work. A pending proposal cancels, invalidates and reserves nothing. | `graph_nodes.accepted_by_change_id` → applied change; T4 copies it to the run |
| I6 | A node has at most one current run. A node with no current run has not started yet, or its last run was superseded. It is **ready** when: the previous live node's current run is completed and holds nothing downstream; the session is `active`; and no attempt in the session is non-terminal (the first and last parts of this rule are current-phase constraints, see below). | Partial unique indexes; readiness is derived |
| I7 | A run's accepted specification is immutable: its definition in the admitting change (found by `node_id`), its frozen execution policy, its `target_version`, and its `resolved_inputs`/prompt frozen at first start. | Triggers |
| I8 | Technical restart → new **attempt** of the same run. Intentional rerun or invalidation → new **run**. Reconnect, reconcile, or resubmitting an uncertain submission → **same** attempt. | T4/T5 rules |
| I9 | One attempt = one execution with one submission key, persisted before dispatch. **At most one non-terminal attempt per run**; `unknown` is non-terminal. An attempt is terminal only once its work is observed stopped. In the current phase the limit is per **session** (see below). | Partial unique index on `run_attempts (run_id)` in every phase, plus `(session_id)` now; T5/T7 rules |
| I10 | Cancellation intent is durable. `cancelled` (and a confirmed timeout) is written only from observed evidence. | `cancel_requested_at` vs state |
| I11 | Only the current attempt of a current run completes the run, writes results or selects outputs. Late results from superseded work stay as history and are never current or selectable. | D24: `cubeai.attempt_id` + `FOR SHARE` in the shared trigger |
| I12 | Downstream work consumes the upstream results pinned per port at its start. Each pin is the current completed run of the selected live upstream node at resolution time (T4), and that pin never changes afterwards, even when the upstream node gets a new run. | `resolved_inputs` (D23), checked in T4 |
| I13 | A change that needs approval is applied only on a **user** decision quoting its exact digest, and only if it is still valid at the current version. The reasons the database can derive (`PLAN_MODE`, `supersedes_completed`) are enforced by the database, whatever the caller sent. | `graph_changes` checks; T1/T2 |
| I14 | Acknowledged input and important notifications are durable before the Main is woken. Handling is tracked by one monotonic cursor per session. | T3; `sessions.inbox_handled_through_event_id` |
| I15 | Finishing the current graph makes the session idle. Nothing is terminally "finished". | Derived activity |
| I16 | A state change and its event commit together. Provenance is never hard-deleted. | `publication()`, RESTRICT |
| I17 | After its run completes, a result's selection never changes. `outputs` is the only selection of a completed run. | D25; selection trigger |
| I18 | Every write to a table that execution writes declares its path, and the trigger checks that path. | D24 |
| I19 | An agent checkpoint is written only by its run's current non-terminal attempt, is read only within its run (or copied once into a new run by a seeded T4), and is never a result: nothing pins, selects or reports from it. | D27; fencing trigger on the checkpoint tables |
| I20 | A suspended attempt holds no running work. It resumes as the same attempt only when the session is `active`, the run is current, and (for a gate) the user's decision is recorded; cancelling it is confirmed at once. | T10; T7 |

#### Current-phase constraints (sequential execution only)

These rules hold **because execution is sequential today**. They are not architecture invariants, and each one has a named replacement when DAG or parallel execution is adopted (§P.5, *No `node_connections` table*):

| Constraint now | Why it holds now | Replacement with a DAG |
|---|---|---|
| At most one non-terminal attempt **per session** (`run_attempts` unique index on `session_id`; a `suspended` attempt counts, so a node waiting on a gate blocks the next node) | Only one node runs at a time, so a second active attempt can only be a stopping superseded run or a duplicate start | Drop the session index; keep the per-run limit, and let resource pools bound parallelism; a suspended node then blocks only its dependants |
| A node is ready when the **previous live node's** current run is completed | `position` is the only dependency order | Ready when every upstream node (by edges) has a current completed run with no active hold |
| Input candidates are the outputs of **earlier** nodes in `position` order (D23) | Earlier = upstream in a sequence | Candidates are the nodes reachable backwards through edges; `input_sources` becomes the mandatory edge |
| The affected set is the **suffix** after the first changed node | Every later node may depend on every earlier one | The nodes reachable from the changed nodes |
| An expectation hold stops the **next** live node | The next node is the only direct dependant | It stops every direct dependant |

#### State ownership (one owner per state)

| State | Owner |
|---|---|
| Session owner | `sessions.user_id` |
| Execution mode, privacy mode, token budget used | `sessions` |
| Control intent (active / paused / archived) | `sessions.control_state` (REST only) |
| Activity (idle / running / pausing / awaiting_user / awaiting_approval / blocked) | **Derived**, published as the `session_state` event, checked in this order: `blocked` = an active downstream hold, a failed current run with no automatic retry left, or an `unknown` attempt; `pausing` = paused with an attempt `preparing`, `submitted` or `running`; `awaiting_user` = a `suspended` attempt on a gate; `running` = an attempt `preparing`, `submitted` or `running`; `awaiting_approval` = a pending change; otherwise `idle`. A pause-suspended attempt shows as `idle` under `control_state = 'paused'` |
| Graph version | `session_graphs.version` |
| Current node definition and order | `graph_nodes` |
| Accepted definition history, approvals, who changed what | `graph_changes` |
| Accepted work not yet started | The live `graph_nodes` row (accepted by `accepted_by_change_id`) with no current run |
| Current execution of a node | The `node_runs` row with `superseded_at IS NULL` |
| Logical run outcome | `node_runs.status`, written only in the attempt transition that causes it |
| Where a run's inputs came from | `node_runs.resolved_inputs` |
| Expectation hold on the next node | `node_runs.downstream_hold` of the run that violated it, released by `hold_released_by_change_id` |
| Execution try, submission identity, uncertainty, cancellation, suspension, error, token usage | `run_attempts` |
| Agent-node reasoning state (messages, finished tool-call results, fan-out progress) | `agent_checkpoints`, `agent_checkpoint_writes` (D27) |
| A gate's decision | The domain gate columns (cohort approval, segmentation review), and `run_attempts.suspension.resume` for every gate (the only record for `tool_approval`) |
| Physical backend state | The backend (Redis/Celery record, MONAI job store) |
| Per-pair segmentation jobs | `segmentation_pairs` |
| Results and lineage | Typed domain rows |
| Port → result selection | While running: the domain selection columns. After completion: `node_runs.outputs` only |
| Conversation and its compaction | `chat_messages` |
| Events and replay | `session_events` |
| Resource occupancy, start-time refusals | Runtime (Executor semaphores, re-evaluated before every start); physical limits belong to Celery and MONAI |

---

### P.5 Table-by-table design

#### `users` (new)
- **Purpose:** The owner of private sessions.
- **Why a table:** One user has many sessions.
- **What breaks without it:** Session lists, WebSocket subscriptions, stored files and recovery are visible to everyone.
- **Affected code:**
  - session create/list/get routes (`routes/sessions.py`, `routes/chat_sessions.py`);
  - every session-scoped route (runs, cohorts, stored files, chats, event replay);
  - the Agent System's `/ws` handshake and `/input`, which ask the Application API whether the caller owns the session.
- **Design:** `users(user_id, username UNIQUE, display_name, created_at)`. Until an authentication mechanism exists, the deployment configures one default user.

#### `sessions` (existing, extended)
- **Purpose:** The owned root scope and the lock target.
- **Design:**
  - add `user_id`, `execution_mode` (default `auto`), `privacy_mode`, `tokens_used`, `inbox_handled_through_event_id`;
  - rename `status` → `control_state`, which holds control intent only (`active|paused|archived`);
  - `sessions.prompt` stays as the immutable opening request and title source. T0 also stores it as the first chat message with a `user_input` event, so the Main reads the opening request from the transcript like every other message;
  - `intent` stays as a read-only legacy column with no new writers, and is dropped with AutoGen.
- **Affected code:** The Agent System stops writing progress statuses (`session_service.py:385-386`), and the status route accepts intent transitions only. A switch to `phi_local` also requests cancellation of attempts whose frozen policy is `standard` (T7).

#### `chats` (existing, one per session)
- **Purpose:** The session's one conversation (D3).
- **Why a table:** It already exists, and `chat_messages`, the routes and `ChatSidebar.tsx` address it by `chat_id`. `UNIQUE (session_id)` makes it one per session without changing any of them.
- **Design:**
  - T0 creates the chat together with the session;
  - `POST /chats/session/{id}` returns the existing chat instead of creating a second one, so `resolveChatId` keeps working;
  - the Main's conversational context is this chat, and its graph context is the session graph.

#### `chat_messages` (existing, contract extended)
- **Purpose:** The durable conversation. `message_id` is the submission idempotency key.
- **Design:**
  1. Input acceptance (T3) inserts the message and a durable `user_input {chat_id, message_id}` event in **one** transaction. A replayed `message_id` with different content returns 409.
  2. Main replies are persisted.
  3. Compaction is a `role='system'` message with `metadata = {v, kind:'context_summary', through_message_id}`.
  4. The versioned `metadata` contract (`v:2`) also carries the Main's tool calls (see *Main loop outputs* below):
     - an assistant row `{kind:'tool_calls', turn_id, calls:[{call_id, tool, args, started_at}]}`, where parallel calls share one row;
     - one `role='tool'` row per result `{kind:'tool_result', turn_id, call_id, tool, status: ok|error|interrupted, ended_at, error?, result | file_id, ui?, phi}`.

     `turn_id` is the `event_id` of the newest event that woke the turn: a `user_input`, or a notification such as `node_failed`. When notifications are coalesced into one turn, it is the highest of their ids. `call_id` pairs each result with its call **within a turn**: models reuse ids such as `call_0` across turns, so the pair key is `(turn_id, call_id)`. `started_at` is known when the call is committed, before it runs, so it lives on the call. `ended_at` and `error {code, detail}` (set when `status = 'error'`) live on the result. `phi` is taken from the tool manifest, so the Context Manager can keep a PHI result out of a context sent to an external model. `args` are stored in full, not as a digest, because the Main rebuilds its context from the transcript after a restart or compaction and must send each call's input back to the model. Together these rows are the audit of a direct call, without a run.
  5. The `text` of a tool row is a short label (the tool name, or the number of calls for `tool_calls`), so `text_not_blank` still holds. The payload lives in `metadata`.

#### Main loop outputs (no table of their own, D21)

| What the Main produces | Stored in | Example |
|---|---|---|
| A reply to the user | `chat_messages` (`role='assistant'`) in the session's chat | "GLCM means …" |
| A simple direct tool call and its result (read-only, inline, not heavy) | An assistant row `metadata = {kind:'tool_calls', turn_id, calls:[{call_id, tool, args, started_at}]}`, then a `role='tool'` row `{kind:'tool_result', turn_id, call_id, status, ended_at, result, ui?, phi}` | "How many chest CTs do we have?" → calls `[{call_id:'c1', tool:'count_studies'}]` → result `{call_id:'c1', status:'ok', result:{count:412}, ui:'table_view'}` |
| A control tool call (`execute`, `graph_apply`) and its result | The `tool_calls` row as above; the `tool_result` row is written **by T1/T2 in the same transaction as the change**, with `result = {change_id, status: applied\|pending, node_ids}` | "Make a cohort of …" → `execute` → result `{change_id, status:'applied', node_ids:[…]}` |
| A large direct result (CSV export, chart image) | `stored_files` (`retained`, `created_by_run_id` NULL); the message metadata carries `file_id` | A patient list exported as CSV |
| Work that writes research results, runs long or waits on a person | `execute(target, params)` → one `graph_changes` row → one `graph_nodes` row → `node_runs`/`run_attempts` at start → typed domain rows | "Make a cohort of …" → Population agent node → `cohorts` with `created_by_run_id` |
| A multi-step proposal | `graph_changes` (`pending` in Plan mode, or when a mandatory reason applies) | "Segment, extract features, train" |
| A modification of existing work (D22) | `graph_changes` with `UpdateParams`/`RerunNode` on the existing node; `supersedes_completed` if a completed run would be superseded | "Add patients over 60 to the cohort" → the Population node and everything after it rerun, after approval |
| A different study (D22) | A **new `sessions` row** (with its graph and its chat), created only after the user accepts the Main's proposal | "Now study liver tumours instead" → new session |

**Rules:**
- **Asking vs saving.** Asking about the population ("how many …?") is a direct read tool with its result in the message. Saving a cohort is always a node, because `cohorts.created_by_run_id` is NOT NULL, the population agent waits on the cohort-approval gate, and in Plan mode the write must be approved.
- **Inline size.** Message metadata holds small results and summaries only. Rows, files and images go to `stored_files` and are referenced by `file_id`.
- **Rendering.** The frontend renders a message whose metadata has `ui` with that view (the tool manifest's `ui` field) instead of plain text. The result is persisted with the message, so it survives refresh and restart.
- **Ordering and pairing.** A turn can call several tools in sequence or in parallel. Every call has a `call_id`, every result names its `call_id`, rows of one turn share `turn_id`, and `seq` gives the total order. A `(turn_id, call_id)` pair has at most one result.
- **Commit order.** The `tool_calls` row is inserted and **committed before** any of its calls runs. The `tool_result` of a direct tool is inserted and committed as soon as its own call ends. The `tool_result` of a control tool is committed **with** the graph change (T1/T2). A tool result is never written after the fact in a separate step from the effect it reports.
- **Interrupted turn.** After a restart, a `tool_calls` row with calls that have no result means the Main stopped mid-turn. Before that input is handled again, the harness writes `tool_result {status:'interrupted', ended_at}` for each such call (`ended_at` is when the harness closed the call, not when the tool stopped), so the transcript never holds an unanswered call. A control call without a result really did not change the graph, because its result commits with the change.
- **Idempotency of control calls.** The `idempotency_key` of a change the Main submits is `sha256(turn_id‖call_id)`. A re-handled turn sees the earlier `execute` and its result in the transcript, so it does not submit the work again.
- **Modify or new (D22).** The Main decides and records it as explicit ops. When unsure it asks, and a different study goes to a new session, never to the end of this sequence.
- **No execution records.** A direct call creates no run or attempt, and is never retried by the Watchdog. If the Main discovers that a call needs durable tracking, it submits a node instead **before** any effect.

#### `session_events` (today's `ws_log`, renamed)
- **Purpose:** Durable, ordered, replayable session events. `event_id` is the client cursor and is allocated under the session lock. `msg_type` is renamed `event_type`.
- **Design:**
  - **New durable types** go in `event_registry.py`: `user_input`, `graph_change_proposed/applied/rejected/stale/expired`, `node_run_superseded`, `run_attempt_started/ended`, `run_attempt_suspended/resumed` and `gate_resolved` (3.2), `node_status` (replacing `pipeline_agent_status`), `expectation_violated`, `node_start_refused`, `graph_idle`, `session_state`, `approval_requested`.
  - **Waking:** whether a type wakes the Main is defined per type, in code. `user_input`, `node_failed`-class `node_status`, `expectation_violated`, `graph_idle` and decided or stale changes wake it. `gate_resolved` wakes the Executor only.
  - **Payloads** carry `node_id`, `run_id`, `attempt_id` and `change_id` where known.
  - **Sweep:** the Agent System periodically re-reads the waking events above `inbox_handled_through_event_id` for active sessions, so an event it was never told about is still handled.

#### `session_graphs` (today's `plans`, renamed and extended)
- **Purpose:** The graph header (1:1 with the session, shared PK) and the CAS row.
- **Design:** Add `version`. `approved_at` is derived from change #1, gets no new writers, and is dropped with AutoGen.

#### `graph_changes` (new)
- **Purpose:** The immutable submitted unit of graph mutation: the full target sequence snapshot, digest, base version, author, approval reasons, decision and applied version.
- **Why a table:**
  - it must survive restart while pending;
  - a decision binds to its content;
  - runs reference it;
  - it is the only history of accepted definitions and approvals.
- **Why not a column on `session_graphs`:** That loses history.
- **Why not in `session_events`:** Runs could not FK it, and the pending lookup and idempotency could not be enforced.
- **Approval reasons, all computed at proposal time:**
  - `PLAN_MODE`: the session is in Plan mode, and the change adds, modifies, reruns or releases work. Removing nodes that never started, and cancelling, need no approval. **The Application API derives this itself** from `sessions.execution_mode`.
  - `supersedes_completed`: applying the change would supersede a completed run (D22). **The Application API derives this itself** from the affected set.
  - `destructive`, `generated_code`, `untrusted_tool`, `approval_required:<code>`: from the target manifests, computed by the Agent System guardrails and sent with the change.
  - A Main direct action that needs approval is promoted to an `AddNode` change **before** its effect.

  In-node scientific gates (cohort approval, segmentation review) stay on their domain rows. Start-time conditions (budget, resource) are not approvals (D19).
- **Design:**
  - **Content** is the full target sequence, with `v`: for every live node, its `node_id` and complete definition; `rerun_node_ids`; `release_hold_node_ids`; `on_running`; an optional `origin` (`composer`/`recovery`) when the Main applies a proposal one of them drafted. **The proposer mints a UUID for every new node**, so there are no placeholders. T1/T2 check that each new id is unused.
  - **Digest:** `content_digest` = SHA-256 of the RFC 8785 canonical JSON of `content`, **computed by the Application API**. A decision must quote it.
  - **One pending change per session.** A new proposal first marks the old one `withdrawn` (or `expired`, if it is past `APPROVAL_TTL_H`), then inserts itself.
  - **Auto apply.** A change with no approval reasons skips T1. T2 inserts it directly as `applied`, and in the same transaction marks any pending change `stale`.

#### `graph_nodes` (today's `plan_steps`, renamed and extended)
- **Purpose:** The stable node (`node_id`), its current definition, and its position in the one sequence.
- **Design:**
  - rename `step_id` → `node_id`, `step_order` → `position`, `agent_type` → `target_name` (an agent **or** a tool), `instructions` → `params`;
  - add `target_kind`, `target_version`, `input_sources`, `expectations`, `removed_at`, `accepted_by_change_id` and `node_seq`;
  - `input_sources` names the source of an input port only when D23 finds several candidates. Otherwise it stays empty, and the single candidate is used;
  - the definition changes only through an applied change, keeping the same `node_id`. A change of `position` counts as a change for the affected set;
  - the node itself is the accepted work until it starts (D8), so the acceptance lives here, not on a pre-created run;
  - a deferrable EXCLUDE constraint keeps live `position` values unique;
  - `node_seq` is assigned by T2 when the node is inserted (the next number for the session) and never reused. The Context Manager shows the Main `n<seq>`.
  - **There is no hold on the node.** An expectation hold lives on the run that caused it (`node_runs.downstream_hold`), so inserting or reordering nodes can never move it. Approval reasons are known at proposal time. Resource and budget refusals are runtime (D19).

#### No `node_connections` table
- **Why:** Execution is sequential (D5), and `position` plus the input rule (D23) fully express the dependencies. Edges are also not hidden in JSON on nodes.
- **Future (DAG):** The design moves to a DAG without a rewrite:
  1. add `node_connections` with DAG validation and backfill one edge per resolved input port (from `input_sources`, or the single candidate);
  2. edges replace the D23 candidate rule as the only input authority; every existing `input_sources` entry is already an edge. `resolved_inputs` keeps its shape;
  3. the affected set becomes "reachable from the changed nodes" (it already lives in one function);
  4. drop the session-wide active-attempt index (a current-phase constraint) and keep the per-run one;
  5. readiness becomes "every upstream run completed with no active hold".

  `position` then becomes display order, and the change content gets a new `v`.

#### `node_runs` (today's `agent_runs`, renamed and extended) = executions
- **Purpose:** One execution of a node's accepted definition, agent or tool, created when the node starts (D8). It anchors every typed result row, `resolved_inputs`, and every job id derived from the run.
- **Why separate from nodes:** Reruns and invalidations keep old results and history.
- **Design:**
  - rename `agent_run_id` → `run_id`, `step_id` → `node_id`, `input_refs` → `resolved_inputs`, `durable_notes`/`warnings`/`output_summary` → `scratchpad_notes`/`scratchpad_warnings`/`scratchpad_summary`;
  - drop `UNIQUE(node_id)`;
  - add `accepted_by_change_id`, `execution_policy`, `target_version`, `superseded_at`, `outputs`, `downstream_hold`, `hold_released_by_change_id`, and status `cancelled`;
  - a row is inserted only by T4, when the node starts, together with attempt 1 (lazy creation, D8). Runs are never `pending`;
  - `status` is a projection of the run's attempts, written in the same transaction and checked at commit by a deferred trigger.
- **`resolved_inputs` contract** (frozen at first start):
  - `{v:1, ports:{<port>:{node_id, run_id, port, type, ref}}, target_version, inference_model_id?, weights_revision?}`.
  - T4 checks, under the session lock, that for every port the pinned `run_id` is **the current completed run of the selected live upstream node at resolution time**: the node is live and earlier in the sequence, it equals the node's `input_sources` entry when one exists, the run has `superseded_at IS NULL` and `status = 'completed'`, and `port` exists in that run's `outputs`. A `render` stored file is never a ref.
- **`outputs` contract** (written with `completed`, immutable afterwards):
  - `{v:1, ports:{<port>:{type, ref:{table, ids}, summary, phi}}}`.
  - T6 checks every ref against an allow-list of tables:
    - **produced** tables (cohorts, segmentation batches, feature sets, model results, reports, `retained` stored files): every id exists and has `created_by_run_id = run_id`;
    - **reference** tables (`volume_series`, `studies`): every id exists;
    - **self** (`node_runs`, for a `ResultSummary`): the id is this run.
- **Expectation hold:**
  - T6 evaluates the node's `expectations` against `outputs[*].summary`. On a violation it sets `downstream_hold = 'expectation_violated'` on **this** run. While `downstream_hold IS NOT NULL AND hold_released_by_change_id IS NULL`, the next live node is not ready.
  - A change releases it with a `ReleaseHold` on this node, which sets `hold_released_by_change_id`. Superseding the run releases it too, because the new run is checked again.
- **Affected code:**
  - `repositories/agent_runs.py`;
  - `resolve_run_for_agent_type` (`agent_runs.py:71`, renamed `resolve_run_for_target`) and its callers (`routes/agent_runs.py:50`, `repositories/cohorts.py:103`);
  - `list_runs`, `agent_status_payload` (renamed `node_status_payload`), session hydration, `execution_state.py`;
  - both run triggers.

#### `run_attempts` (new)
- **Purpose:** One real execution try of a run: submission key, request, lifecycle including `unknown`, dispatch time, cancellation intent, error, token usage and times.
- **Why a table:**
  - a run can be tried several times;
  - each try needs its own submission identity, uncertainty state, cancellation and error history;
  - "one non-terminal attempt per session" must be enforced by a constraint.
- **Per node kind:**

  | Node | Attempt execution | Backend identity |
  |---|---|---|
  | Agent node | In-process agent task (`dispatch_kind='in_process'`); may be `suspended` on a gate or a pause, with no task (D27) | None external. Internal jobs are keyed through the run: segmentation pair jobs through the run's batch (D10), radiomics jobs through `run_id` in their job ids. They therefore survive into the next attempt and are fenced by run (D24). The agent's own state survives through the run's checkpoint thread |
  | Tool node, `execution=job` | One Celery job | `submission_key = {sha256(run_id\|tool\|args)}.{attempt_number}`. It is also the Celery task id and the key of the Redis job record, so reconciliation finds exactly this attempt |
  | Tool node, `execution=inline` | Inline in the Executor (`dispatch_kind='inline'`) | None |

- **Lifecycle:**
  - `preparing → submitted → running → succeeded | failed | cancelled | interrupted`, plus `unknown`. `preparing → running` is allowed, because a worker that starts is the evidence that submission happened.
  - `running ⇄ suspended` for `in_process` agent attempts only (T10). `suspension` holds `{v, kind: gate|pause, gate?, interrupt_id, payload, checkpoint_id, since, resume?}`; `resumed_at` is set on each resume. `suspended → cancelled` (or `failed` with `gate_timeout`) is confirmed in one step by T7.
  - `dispatched_at` is set when the attempt leaves `preparing`. After an interruption it still says whether the work may already have had effects.
  - A timeout sets `cancel_requested_at` and `error.code='timeout'` while the state stays non-terminal. Once the stop is confirmed, the attempt becomes `failed` with that error.
  - `usage` (`{v, input_tokens, output_tokens, model}`) is written when the attempt ends and added to `sessions.tokens_used` in the same transaction.
- **Affected code:** A new repository and routes. The Node Runner opens, dispatches, reconciles and closes attempts.

#### `stored_files` (today's `artifacts`, renamed)
- **Purpose:** The bytes store for figures and CSVs. `artifact_id` is renamed `file_id`.
- **Rule:** `render` rows are regenerable previews and are never pinned as scientific inputs (D18). File inputs must be `retained` stored files or typed rows.
- **Without a run:** `created_by_run_id` is nullable. A file produced by a direct Main tool call (D21) is stored with no run and referenced from the chat message.

#### No `run_artifacts` table
- **Why:** Inputs are pinned per port in `resolved_inputs`. Outputs are the typed rows plus the frozen `outputs` port map on the run. Selection lives in the domain selection columns while the run is running and in `outputs` after it completes (D25). There are no graph-level checkpoints; agent checkpoints (D27) are reasoning state inside one run and never carry results between nodes.
- **Future:** Add it when untyped files flow between nodes, or when a resumable tool node needs attempt-scoped checkpoints.

#### `agent_checkpoints`, `agent_checkpoint_writes` (new, revision 3.2, D27)
- **Purpose:** The LangGraph checkpoints of resumable agent nodes, and the pending writes that belong to each checkpoint (finished tool-call tasks and fan-out branches of a step that had not completed).
- **Why tables in the Application API:** Only the Application API owns PostgreSQL (D1), and the writes must be fenced by attempt (D24) so that a superseded attempt never writes state a later attempt resumes.
- **Why not on `run_attempts`:** A run has many checkpoints, read by every attempt of the run; one column per attempt would lose history and the parent chain LangGraph needs.
- **Design:**
  - key: `(run_id, checkpoint_ns, checkpoint_id)` for checkpoints and `(run_id, checkpoint_ns, checkpoint_id, task_id, idx)` for writes. `run_id` is the LangGraph `thread_id`; `checkpoint_ns` is `''` for the agent and the subgraph namespace for a workflow step;
  - `attempt_id` records the writing attempt; `checkpoint` / `value` hold the serializer output (`type` + bytes), opaque to the Application API;
  - `metadata` (JSONB, `v`) holds what the database reads: `engine_version`, `spec_digest`, `step`, `pending_tools [{name, idempotent}]`, and `seeded_from` on a seeded copy;
  - **writes:** the attempt path, by the run's current attempt in state `running` or `suspended` (a `node_message` delivered to a suspended node is written with `aupdate_state`);
  - **reads:** any attempt of the run, the Recovery Agent through `/runs/{run_id}/steps` (metadata only), and T4 when it copies a `seed_from` checkpoint;
  - **purge:** the harness path deletes the rows of runs terminal for more than `AGENT_CHECKPOINT_RETENTION_DAYS` that no live `seed_from` names. Nothing else deletes them.
- **Affected code:** new models, a repository and routes (Phase 6.3, 6.4); `execution/checkpointer.py` (Phase 9.5).

#### Typed domain tables (existing; shared trigger rewritten)
- **Producer:** the producer is the node's target name, through the existing join, with an allow-list on every result table: `cohorts` (`study_population_planner_agent`), `segmentation_batches` (`segmentation_agent`, `run_segmentation_with_guard`), `reports` (`latex_reporter_agent`), `feature_sets` and `radiomics_features` (`feature_extraction_agent`, `radiomics_extract_features`), `model_results` (`run_modeling`, the modeling job tools, legacy `modeling_agent`). `stored_files` has no list, because any run may store a file, and `segmentation_pairs` none, because a pair is owned through its batch. A new graph target that produces one of these tables joins its list with its own migration, and the Phase 6.5 contract test checks every name against the catalog.
- **Fencing (D24), on every INSERT and UPDATE.** The trigger first reads the declared path, and rejects the write when there is none (the paths are in the table below). A late row from a superseded run's still-running attempt is therefore accepted and kept as history, keyed to the old run. A row from an attempt that is no longer the run's active one is rejected.

| Path (setting) | Who uses it | Allowed writes | Check |
|---|---|---|---|
| **attempt** (`cubeai.attempt_id`) | The node's own attempt: agent tools, job workers, the Node Runner | Inserts of result rows; selection/approval columns; pair bookkeeping; `stored_files`; `node_runs.scratchpad_*` | `SELECT … FROM run_attempts WHERE attempt_id = … FOR SHARE`; the attempt belongs to the row's run and is in `submitted` or `running` |
| **internal job** (`cubeai.run_id`) | Jobs that outlive one attempt (radiomics features, segmentation pair jobs) | Run-scoped natural-key inserts and pair bookkeeping only | The run is not completed; a repeated write is a no-op through the natural key |
| **attempt**, checkpoint tables (`cubeai.attempt_id`) | The Node Runner's `HttpCheckpointSaver` (D27) | Inserts into `agent_checkpoints` / `agent_checkpoint_writes` of the attempt's own run | The attempt row `FOR SHARE`; it belongs to the run, is the run's latest attempt, and is `running` or `suspended` |
| **user gate** (`cubeai.actor = 'user'`) | The owner's decision in an in-node gate (cohort approval, segmentation review, tool approval), through a route that checks ownership | `cohorts.frozen_at`, `segmentation_pairs.review_decision`/`selected_segmentation_uid`, `segmentation_batches.review_status`, and `run_attempts.suspension.resume` of the run's suspended attempt only | The run is `running` and current; the attempt is `suspended` on that gate |
| **harness** (`cubeai.actor = 'harness'`) | T2, T5, T6 (run projection), T7, T10 suspend/resume, the checkpoint purge | Lifecycle columns of `node_runs` and `run_attempts` only, never domain rows; deleting purgeable checkpoint rows | Runs under the session lock |
| **migration** (`cubeai.actor = 'migration'`) | Alembic only | Backfills | — |

- **Coverage is tested, not assumed:**
  - a contract test lists every table with `created_by_run_id`, plus `segmentation_pairs` (it has no run column, but the pair review and selection columns are D25 gate columns, so its run is resolved through the batch), `stored_files`, `node_runs`, `run_attempts`, `agent_checkpoints` and `agent_checkpoint_writes`, and asserts in `pg_trigger` that the fencing trigger is attached to each;
  - an integration test per path shows that an undeclared write is rejected;
  - a static scan requires every repository function that writes those tables to open its transaction through one helper that sets the path.
- **Selection and approval changes** also require the run to be `running` and current (`superseded_at IS NULL`) (D25). These are:
  - `model_results.selected`;
  - `segmentation_pairs.selected_segmentation_uid`/`review_decision`;
  - `segmentation_batches.review_status → submitting/submitted`;
  - `cohorts.frozen_at` and `feature_sets.frozen_at`.

  For the same reason, the cohort alignment trigger no longer freezes a cohort on its first downstream use (the n1 behaviour): that would set `frozen_at` after the cohort's own run completed. A batch, feature set or model result that names an unfrozen cohort is refused, and the cohort is frozen only by its approval gate. The consumers already refuse an unfrozen cohort before they write (`population_handoff.py`, `targets_matcher.py`, the radiomics tools, the segmentation module).
- **Execution bookkeeping** (pair `status`, `execution_checkpoint`) is allowed for any run that is not completed, including a superseded one that is still stopping.
- **Renamed:**
  - `created_by_agent_run_id` → `created_by_run_id` in every domain table;
  - `segmentation_runs` → `segmentation_batches` (`segmentation_run_id` → `segmentation_batch_id` in targets and pairs);
  - `model_results.predictions_artifact_id`/`input_artifact_id` → `predictions_file_id`/`input_file_id`; `report_images.artifact_id` → `file_id`;
  - `radiomics_features.id` → `radiomics_feature_id`;
  - the batch config key `correlation_run_id` → `batch_correlation_id`.
- **Fresh reruns:** a run created for a node that the accepting change reran (`RerunNode`) has `execution_policy.fresh = true`. Radiomics must then not reuse content-addressed rows produced by other runs.

#### `segmentation_pairs` (existing; one column renamed)
- **Purpose:** Per-item backend identity, `inference_job_id` (renamed from `external_job_id`). It is derived from the batch correlation id (D10), never re-pointed, and stored with a checkpoint.
- **Prerequisite:** MONAI must support an explicit re-queue of a terminal job under the same id (with an incremented internal attempt) before a failed pair can be re-executed within the same run.

#### `training_jobs` (new)
- **Purpose:** A durable receipt for standalone nnUNet training, which runs outside the session graph.
- **What breaks without it:**
  - a lost response followed by a retry means a second full training;
  - the job cannot be cancelled or inspected;
  - `workflow.frozen_at` can never be lifted safely.
- **Rule:** If training ever becomes graph work, it becomes a tool-node attempt. This table then stops receiving rows, and it never becomes a second owner of the same execution.

#### `inference_models`, `segmentation_masks`, `workflow`, `workflow_details` (contents unchanged)
`inference_models` and `segmentation_masks` are today's `models` and `segmentations`, renamed. `workflow` and `workflow_details` keep their names: in the schema, *workflow* means only an nnUNet training task. `model_id` becomes `inference_model_id` wherever it appears. Runs pin the inference model and weights revision they used in `resolved_inputs`.

---

### P.6 Column-by-column design

Legend: KEEP · MODIFY · ADD · DERIVE · MOVE · NOT STORED

#### `users`
| Column | Verdict | Reason |
|---|---|---|
| `user_id` UUID PK | ADD | Owner key |
| `username` TEXT NOT NULL UNIQUE | ADD | The stable login name an authentication mechanism maps to; case-normalised |
| `display_name` TEXT NULL | ADD | UI |
| `created_at` | ADD | |
| roles, email, password hash, teams, sharing | NOT STORED | Not required. Credentials never live here |

#### `sessions`
| Column | Verdict | Reason |
|---|---|---|
| `session_id`, `title`, `created_at`, `updated_at`, `error` | KEEP | |
| `user_id` UUID NOT NULL FK → `users` | ADD | Single owner (I1). Immutable. Existing rows go to an explicit `legacy` user |
| `prompt`, `prompt_kind` | KEEP | The opening request and title source. T0 also writes it as the first chat message |
| `intent` | KEEP (legacy, read-only) | No new writers. Dropped with AutoGen |
| `control_state` (renamed from `status`) | MODIFY | `active\|paused\|archived`. `pausing` and every progress value are derived (D13) |
| `execution_mode` TEXT NOT NULL DEFAULT 'auto' | ADD | Read by T1/T2 (`PLAN_MODE`) and the guardrails. Must survive restart |
| `privacy_mode` TEXT NOT NULL DEFAULT 'standard' | ADD | Read by the Model Gateway and Context Manager, and frozen into each run's policy. Losing it would weaken PHI protection |
| `tokens_used` BIGINT NOT NULL DEFAULT 0 | ADD | The session budget counts Main, Composer, Recovery and node calls. Advanced once per attempt end (T5/T6/T7) and once per Main turn (T8), never per model call, because this row is the lock root |
| `inbox_handled_through_event_id` BIGINT NULL | ADD | Monotonic. Advanced only after every waking event ≤ n has been handled. One cursor is exact because the Main is serialized per session |
| context summary | MOVE → system message in the session's chat | The compaction boundary stays inside the transcript |

#### `chats`, `chat_messages`
| Column | Verdict | Reason |
|---|---|---|
| `chats.*` | KEEP | One per session (D3) |
| `UNIQUE (session_id)` on `chats` | ADD | Enforces one chat per session. Dropping it later is the only change needed to allow many chats |
| `chats.last_activity_at` (renamed from `last_activity`) | MODIFY | `*_at` convention |
| `chat_messages.message_id` | KEEP | Idempotency key for input acceptance |
| `chat_id`, `text` | KEEP | A tool row's `text` is a short label; its payload is in `metadata` |
| `role` | MODIFY | Add `tool` for tool results: `user\|assistant\|system\|tool` |
| `seq` BIGINT GENERATED ALWAYS AS IDENTITY | ADD | Total order of the transcript. Today's order is `timestamp, message_id`; rows written in one transaction share `now()` and then fall back to random UUID order, which can swap a call and its result |
| `created_at` (renamed from `timestamp`) | MODIFY | `*_at` convention; `timestamp` is a SQL type name |
| `metadata` (renamed from `extra_data`; ORM attribute `metadata_`) | MODIFY | Versioned contract (`v:2`): `tool_calls` and `tool_result` rows paired by `(turn_id, call_id)` (calls carry `args` and `started_at`; results carry `ended_at`, `error` and `phi`), and the `context_summary` boundary |

#### `session_events`
| Column | Verdict | Reason |
|---|---|---|
| `event_id`, `session_id`, `payload`, `created_at` | KEEP | Commit-ordered per session under the session lock |
| `event_type` (renamed from `msg_type`) | MODIFY | It is an event type; the envelope already calls it `type` |
| per-session sequence, dedupe key, run/attempt/change FKs, per-row wake flag | NOT STORED | `event_id` already orders. Logical dedupe belongs to the entity (message, change, submission key). Ids travel in the payload. Waking is a property of the event type |

#### `session_graphs`
| Column | Verdict | Reason |
|---|---|---|
| `session_id`, `updated_at` | KEEP | |
| `version` INT NOT NULL DEFAULT 0 | ADD | CAS token. Changed only by T2 |
| `approved_at` | DERIVE → retire | Equals `decided_at` of the change with `applied_version = 1`. No new writers; dropped with AutoGen |

#### `graph_changes`
| Column | Verdict | Reason |
|---|---|---|
| `change_id` UUID PK | ADD | Runs FK it; `/approve <id>` addresses it |
| `session_id` FK → `session_graphs` | ADD | |
| `author` TEXT NOT NULL (`main\|user\|harness\|legacy`) | ADD | Who submitted it. A proposal drafted by the Composer or the Recovery Agent is submitted by the Main; `content.origin` records the drafter |
| `idempotency_key` TEXT NOT NULL | ADD | `UNIQUE(session_id, key)`. The UI uses the Idempotency-Key header; the Main uses `sha256(turn_id‖call_id)`. A replay with a different digest returns 409 |
| `base_version` INT NOT NULL | ADD | Stale detection |
| `content` JSONB NOT NULL | ADD | Versioned (`v`). The **full target sequence**: every live `node_id` (new ones minted by the proposer) with its complete definition, `rerun_node_ids`, `release_hold_node_ids`, `on_running`, optional `origin`. Immutable. A run finds its definition here by `node_id` |
| `content_digest` TEXT NOT NULL | ADD | SHA-256 of the RFC 8785 canonical JSON of `content`, computed by the Application API. A decision must quote it |
| `approval_reasons` TEXT[] NOT NULL DEFAULT '{}' | ADD | `PLAN_MODE` and `supersedes_completed` (derived by the Application API, I13), `destructive`, `generated_code`, `untrusted_tool`, `approval_required:<code>` (from the manifests). Empty means it may apply without a human |
| `status` (`pending\|applied\|rejected\|stale\|withdrawn\|expired`) | ADD | One state machine; approval and apply happen in one transaction |
| `decided_by` (`user\|auto`) NULL, `decided_at` NULL | ADD | Changes with approval reasons can only be decided by a user |
| `applied_version` INT NULL | ADD | `= base_version + 1`. There is no auto-rebase: a stale proposal is re-proposed |
| `created_at` | ADD | The approval TTL (`APPROVAL_TTL_H`) is measured from it |

#### `graph_nodes`
| Column | Verdict | Reason |
|---|---|---|
| `node_id` (renamed from `step_id`), `session_id` | KEEP | Stable identity, minted in the proposal that adds the node |
| `node_seq` INT NOT NULL | ADD | Short per-session handle (`n3`) for the Main and the UI. Assigned by T2, never reused. Backfill = `position` |
| `position` (renamed from `step_order`) | MODIFY | The execution order for live nodes. Mutable only by an applied change. Deferrable EXCLUDE over live nodes |
| `target_name` (renamed from `agent_type`) | MODIFY | Agent type or tool name. Immutable per node. Read by the producer trigger |
| `target_kind` TEXT NOT NULL DEFAULT 'agent' | ADD | `agent\|tool`. Selects the runner and whether a prompt is required. Immutable |
| `target_version` TEXT NOT NULL DEFAULT 'legacy' | ADD | Registry version when the node was added or last edited. The version actually used is frozen on the run |
| `title`; `params` (renamed from `instructions`) | MODIFY | Current definition, changed only through an applied change |
| `input_sources` JSONB NULL | ADD | Versioned. `{port: {node_id, port}}`, naming the source of an input port that has several candidates (D23). Part of the definition: it is in the change content, and changing it is an edit. T2 checks every entry names a live node earlier in the sequence |
| `expectations` JSONB NULL | ADD | Versioned. Checked by T6 against this node's output summaries; a violation sets `downstream_hold` on its run |
| `accepted_by_change_id` UUID NOT NULL | ADD | FK to the applied change that last added, edited or requested a rerun of this node. T4 copies it into the run. Backfill: change #1 |
| `removed_at` TIMESTAMPTZ NULL | ADD | One-way tombstone |
| `hold_reason` | NOT STORED | Expectation holds live on the run; approval reasons are known at proposal time; resource and budget refusals are runtime (D19) |
| current run pointer, node status, readiness, definition revision, fingerprint | DERIVE | Partial unique index; runs, attempts and holds; `accepted_by_change_id` + `node_id`; not needed while invalidation is the sequential suffix |
| edges | NOT STORED | Sequential only (D5) |

#### `node_runs`
| Column | Verdict | Reason |
|---|---|---|
| `run_id` (renamed from `agent_run_id`), `session_id` | KEEP | |
| `node_id` (renamed from `step_id`) | MODIFY | Drop `UNIQUE`; add `UNIQUE(node_id) WHERE superseded_at IS NULL` |
| `accepted_by_change_id` UUID NOT NULL | ADD | The node's `accepted_by_change_id` at start: which accepted definition this run executes (I5, I7) |
| `execution_policy` JSONB NOT NULL | ADD | Versioned. Frozen at run creation from the manifest and the session: `max_attempts`, `timeout_s`, `idempotent`, `fresh` (true when the accepting change reran this node), `privacy_mode`, and for agent nodes `resumable`, `gate_timeout_s` and `seed_from {run_id, checkpoint_id, applied, reason?}` (D27). A deploy, restart or settings change after that cannot change it |
| `target_version` TEXT NOT NULL | ADD | The registry version actually executed. A column rather than a JSON key, so history can be queried by version |
| `status` | MODIFY | `running\|completed\|failed\|cancelled`. A projection of attempts (any non-terminal → `running`; latest `succeeded` → `completed`; latest `failed`/`interrupted` → `failed`; latest `cancelled` → `cancelled`), written only in the attempt-transition transaction and checked by a deferred trigger. A late success after supersession is `completed` + `superseded_at` (history) |
| `superseded_at` TIMESTAMPTZ NULL | ADD | Set by T2. Removes current-ness and selection rights. For a running run it also sets `cancel_requested_at` on the active attempt. May be set on a completed run |
| `outputs` JSONB NULL | ADD | `{v, ports:{port:{type, ref:{table, ids}, summary, phi}}}`, written with `completed`, validated by T6 and immutable afterwards. Read by the next node, `artifact_read`, expectations and the PHI filter |
| `downstream_hold` TEXT NULL | ADD | `expectation_violated`. Set once, by T6, on a completed current run |
| `hold_released_by_change_id` UUID NULL | ADD | Set once, by T2, when an applied change releases the hold. Keeps the audit of who released it |
| `compiled_prompt` | KEEP; MODIFY rule | Required at first start only for `target_kind='agent'` |
| `resolved_inputs` (renamed from `input_refs`) | MODIFY | Port-level pins `{v, ports:{…}, target_version, …}` (D23), frozen at first start and checked by T4. Never a `render` stored file |
| `scratchpad_notes`, `scratchpad_warnings`, `scratchpad_summary` (renamed from `durable_notes`, `warnings`, `output_summary`) | KEEP | Agent scratchpad (labels, not authority), clearly separate from the authoritative `outputs` |
| `error` | DERIVE (projection) | Copy of the last attempt's error, written in the same transaction |
| `started_at`, `completed_at` | KEEP | First attempt start; logical terminal time, including `cancelled` |
| fingerprint, reuse link | NOT STORED (now) | With sequential suffix invalidation, the unaffected prefix keeps its current runs, and that is the reuse. Add both together when cross-version caching is built |

#### `run_attempts`
| Column | Verdict | Reason |
|---|---|---|
| `attempt_id` UUID PK | ADD | The fencing token every execution write presents (D24) |
| `run_id`, `session_id` | ADD | Composite FK → `node_runs`. `session_id` carries the session-wide active-attempt index |
| `attempt_number` SMALLINT NOT NULL | ADD | `UNIQUE(run_id, attempt_number)`. Compared with `execution_policy.max_attempts` |
| `dispatch_kind` TEXT NOT NULL | ADD | `in_process\|job\|inline\|legacy`. Decides how to reconcile after a restart. `legacy` only on backfilled rows |
| `submission_key` TEXT NULL | ADD | Job attempts: `{job_id}.{attempt_number}`, which is also the Celery task id and the Redis record key. Unique and set-once. Persisted **before** dispatch, and reused for any resubmission of the same attempt |
| `request` JSONB NULL | ADD | Job attempts: the exact submitted request (tool, args, context), so a lost request can be re-sent identically. Bounded |
| `state` TEXT NOT NULL | ADD | `preparing → submitted → running → succeeded\|failed\|cancelled\|interrupted`, plus `unknown` and `suspended` (`running ⇄ suspended`, agent attempts only, D27); `preparing → running` allowed. Updated only from observed evidence |
| `suspension` JSONB NULL | ADD (3.2) | Versioned. `{kind: gate\|pause, gate?, interrupt_id, payload, checkpoint_id, since, resume?}`. Set by T10 suspend; `resume` set by the gate decision (user gate path). Kept after resume as the last suspension; every suspension is also a durable event |
| `resumed_at` TIMESTAMPTZ NULL | ADD (3.2) | Set by each T10 resume. The per-attempt timeout counts from the latest of `started_at` and `resumed_at`, so suspended time never counts |
| `dispatched_at` TIMESTAMPTZ NULL | ADD | Set when the attempt leaves `preparing`. After `interrupted` it tells T4 whether the work may already have had effects (retry rule f) |
| `cancel_requested_at` TIMESTAMPTZ NULL | ADD | Durable intent, valid in any non-terminal state, including `preparing` and `unknown`. Also set by a timeout |
| `error` JSONB NULL | ADD | `{code: error\|timeout\|queue_timeout\|oom\|interrupted\|cancelled\|run_not_running\|max_iterations\|resource_unavailable\|record_lost, detail}`. May be set while non-terminal (a pending timeout) |
| `usage` JSONB NULL | ADD | `{v, input_tokens, output_tokens, model}`, written when the attempt ends |
| `started_at`, `ended_at` | ADD | Per-try timing. The per-attempt timeout uses these; the total cap uses the run's `started_at` |
| raw backend status, receipts, controller fence | NOT STORED | The backend owns raw status; `state` + `error` hold the confirmed outcome (D12, D19). Agent resume state and review continuation are stored since 3.2, in `agent_checkpoints` and `suspension` (D27) |

#### `agent_checkpoints` (new, 3.2)
| Column | Verdict | Reason |
|---|---|---|
| `run_id` UUID, `session_id` UUID | ADD | Composite FK → `node_runs`. `run_id` is the LangGraph `thread_id` |
| `checkpoint_ns` TEXT NOT NULL DEFAULT '' | ADD | `''` for the agent; the subgraph namespace for a workflow step |
| `checkpoint_id` TEXT NOT NULL | ADD | LangGraph's id, monotonic within a thread. PK = `(run_id, checkpoint_ns, checkpoint_id)` |
| `parent_checkpoint_id` TEXT NULL | ADD | The parent chain that `alist` and time travel walk |
| `attempt_id` UUID NOT NULL FK → `run_attempts` | ADD | The writer; checked by the fencing trigger |
| `type` TEXT NOT NULL, `checkpoint` BYTEA NOT NULL | ADD | Serializer output, opaque to the database |
| `metadata` JSONB NOT NULL | ADD | Versioned: `engine_version`, `spec_digest`, `step`, `pending_tools`, `seeded_from?`. The only part the database reads |
| `created_at` | ADD | Retention and ordering |

#### `agent_checkpoint_writes` (new, 3.2)
| Column | Verdict | Reason |
|---|---|---|
| `run_id`, `session_id`, `checkpoint_ns`, `checkpoint_id` | ADD | FK → `agent_checkpoints` |
| `task_id` TEXT, `idx` INT | ADD | PK = `(run_id, checkpoint_ns, checkpoint_id, task_id, idx)`; a replayed write is a no-op |
| `channel` TEXT, `type` TEXT, `value` BYTEA | ADD | One pending write: a finished task or fan-out branch of a step that did not complete |
| `attempt_id` UUID NOT NULL | ADD | The writer; fenced |
| `created_at` | ADD | |

#### `training_jobs`
| Column | Verdict | Reason |
|---|---|---|
| `job_id` TEXT PK | ADD | Minted and committed before the POST. MONAI's dedupe key |
| `task_name` FK → `workflow` | ADD | |
| `created_at` | ADD | |
| `submitted_at` NULL | ADD | NULL means uncertain. Reconcile: 404 → resubmit the same id; found → set it |
| `cancel_requested_at` NULL | ADD | Allowed even while the submission is uncertain |
| `ended_at`, `outcome` (`completed\|failed\|cancelled`) NULL | ADD | Confirmed terminal outcome |
| `error` JSONB NULL | ADD | |

---

### P.7 Final schema

Only new or changed tables are shown.

```text
users                                          -- NEW
  user_id                        uuid PK
  username                       text NOT NULL UNIQUE
  display_name                   text NULL
  created_at                     timestamptz NOT NULL

sessions
  session_id                     uuid PK
  user_id                        uuid NOT NULL FK→users
  title, prompt, prompt_kind, intent(legacy, read-only), error, created_at, updated_at
  control_state                  text  active|paused|archived
  execution_mode                 text NOT NULL DEFAULT 'auto'
  privacy_mode                   text NOT NULL DEFAULT 'standard'
  tokens_used                    bigint NOT NULL DEFAULT 0
  inbox_handled_through_event_id bigint NULL

chats                                          -- one per session (UNIQUE session_id)
  chat_id, session_id, title, created_at, last_activity_at

chat_messages
  message_id, chat_id, seq (identity), role (user|assistant|system|tool),
  text, metadata, created_at

session_events                                 -- was ws_log
  event_id, session_id, event_type, payload, created_at

session_graphs                                 -- graph header (was plans)
  session_id                     uuid PK FK→sessions
  version                        int NOT NULL DEFAULT 0
  updated_at

graph_changes                                  -- NEW
  change_id                      uuid PK
  session_id                     uuid NOT NULL FK→session_graphs
  author                         text NOT NULL    main|user|harness|legacy
  idempotency_key                text NOT NULL
  base_version                   int NOT NULL
  content                        jsonb NOT NULL   full target sequence, node ids minted; immutable
  content_digest                 text NOT NULL    sha256 of RFC 8785 JSON, computed by the API
  approval_reasons               text[] NOT NULL DEFAULT '{}'
  status                         text NOT NULL    pending|applied|rejected|stale|withdrawn|expired
  decided_by                     text NULL        user|auto
  decided_at                     timestamptz NULL
  applied_version                int NULL
  created_at                     timestamptz NOT NULL

graph_nodes                                    -- stable nodes, one sequence (was plan_steps)
  node_id                        uuid PK
  session_id                     uuid FK→session_graphs
  node_seq                       int NOT NULL     short handle, never reused
  position                       int              the execution order (live nodes)
  target_kind                    text NOT NULL DEFAULT 'agent'   agent|tool; immutable
  target_name                    text NOT NULL                   immutable
  target_version                 text NOT NULL DEFAULT 'legacy'
  title, params                                   current definition
  input_sources                  jsonb NULL       explicit source of an ambiguous input port
  expectations                   jsonb NULL
  accepted_by_change_id          uuid NOT NULL FK→graph_changes
  removed_at                     timestamptz NULL

node_runs                                      -- executions, created at start (was agent_runs)
  run_id                         uuid PK
  node_id, session_id            composite FK→graph_nodes
  accepted_by_change_id          uuid NOT NULL
  execution_policy               jsonb NOT NULL   incl. privacy_mode
  target_version                 text NOT NULL
  status                         text  running|completed|failed|cancelled
  superseded_at                  timestamptz NULL
  outputs                        jsonb NULL
  downstream_hold                text NULL        expectation_violated
  hold_released_by_change_id     uuid NULL FK→graph_changes
  compiled_prompt, resolved_inputs                frozen at first start (port-level)
  scratchpad_notes, scratchpad_warnings, scratchpad_summary
  error (projection), started_at, completed_at

run_attempts                                   -- NEW
  attempt_id                     uuid PK
  run_id, session_id             composite FK→node_runs
  attempt_number                 smallint NOT NULL
  dispatch_kind                  text NOT NULL    in_process|job|inline|legacy
  submission_key                 text NULL
  request                        jsonb NULL
  state                          text NOT NULL
  dispatched_at                  timestamptz NULL
  cancel_requested_at            timestamptz NULL
  error                          jsonb NULL
  usage                          jsonb NULL
  suspension                     jsonb NULL       gate | pause (3.2)
  resumed_at                     timestamptz NULL (3.2)
  started_at, ended_at           timestamptz NULL

agent_checkpoints                              -- NEW (3.2): agent reasoning state, never results
  run_id, session_id             composite FK→node_runs   (run_id = LangGraph thread_id)
  checkpoint_ns                  text NOT NULL DEFAULT ''
  checkpoint_id                  text NOT NULL            PK (run_id, checkpoint_ns, checkpoint_id)
  parent_checkpoint_id           text NULL
  attempt_id                     uuid NOT NULL FK→run_attempts (writer)
  type, checkpoint               text, bytea               serializer output
  metadata                       jsonb NOT NULL           engine_version, spec_digest, step, pending_tools
  created_at                     timestamptz NOT NULL

agent_checkpoint_writes                        -- NEW (3.2): pending writes of a checkpoint
  run_id, session_id, checkpoint_ns, checkpoint_id   FK→agent_checkpoints
  task_id, idx                   PK with the above
  channel, type, value           text, text, bytea
  attempt_id                     uuid NOT NULL
  created_at                     timestamptz NOT NULL

stored_files                                   -- was artifacts (file_id, created_by_run_id, …)
segmentation_batches                           -- was segmentation_runs
segmentation_masks                             -- was segmentations
inference_models                               -- was models
workflow ─< workflow_details                   -- kept (nnUNet training task, frozen cases)

training_jobs                                  -- NEW (standalone training)
  job_id, task_name FK→workflow, created_at, submitted_at,
  cancel_requested_at, ended_at, outcome, error
```

```text
users ─< sessions ─┬─< chats ─< chat_messages
                   ├─< session_events
                   └── session_graphs(version) ─┬─< graph_changes
                                                └─< graph_nodes ordered by position (live)
                                                      Node 1 → Node 2 → Node 3
                                                      └─< node_runs ─< run_attempts
                                                            ├─< agent_checkpoints ─< agent_checkpoint_writes  (3.2)
                                                            │  (resolved_inputs pin earlier runs per port)
                                                            └─< domain rows (created_by_run_id)
```

---

### P.8 Constraints and transaction boundaries

#### Constraints

```text
users
  UNIQUE (username)

sessions
  FK user_id → users (RESTRICT); TRIGGER user_id immutable
  CHECK control_state IN ('active','paused','archived')
  CHECK execution_mode IN ('plan','auto'); privacy_mode IN ('standard','phi_local')
  TRIGGER tokens_used, inbox_handled_through_event_id monotonic

chats
  UNIQUE (session_id)                                  -- one chat per session (D3)

chat_messages
  CHECK role IN ('user','assistant','system','tool')
  UNIQUE (seq)
  UNIQUE (chat_id, (metadata->>'turn_id'), (metadata->>'call_id'))
         WHERE metadata->>'kind' = 'tool_result'      -- one result per call, per turn
  TRIGGER a tool_result's call_id must appear in an earlier tool_calls row
          of the same chat and turn_id

graph_changes
  UNIQUE (session_id, idempotency_key)
  UNIQUE (session_id, applied_version)
  UNIQUE (session_id) WHERE status = 'pending'
  UNIQUE (change_id, session_id)                       -- runs' composite FK target
  CHECK status IN ('pending','applied','rejected','stale','withdrawn','expired')
  CHECK (status = 'applied') = (applied_version IS NOT NULL)
  CHECK applied_version IS NULL OR applied_version = base_version + 1
  CHECK (status IN ('applied','rejected')) = (decided_at IS NOT NULL AND decided_by IS NOT NULL)
  CHECK cardinality(approval_reasons) = 0 OR decided_by IS DISTINCT FROM 'auto'
  CHECK content_digest ~ '^[0-9a-f]{64}$'
  CHECK content ? 'v'
  TRIGGER content, content_digest, base_version, author, approval_reasons immutable;
          terminal statuses immutable; no DELETE
  T1/T2   add the derivable reasons (PLAN_MODE, supersedes_completed) themselves, whatever the caller sent

graph_nodes
  EXCLUDE USING btree (session_id WITH =, position WITH =)
          WHERE (removed_at IS NULL) DEFERRABLE INITIALLY IMMEDIATE
  UNIQUE  (session_id, node_seq)
  TRIGGER node_id, session_id, node_seq, target_name, target_kind immutable; removed_at one-way;
          definition/order changes only in a txn that advances session_graphs.version
          (a deferred check at commit, so T2 may advance the version at step 8)
  APPLY   validates one contiguous live sequence (1..n); no branches, no joins;
          every input_sources entry names a live node with a smaller position
  FK      (accepted_by_change_id, session_id) → graph_changes(change_id, session_id)

node_runs
  UNIQUE (node_id) WHERE superseded_at IS NULL                        -- I6 (≤1)
  FK     (accepted_by_change_id, session_id) → graph_changes(change_id, session_id)
  FK     (hold_released_by_change_id, session_id) → graph_changes(change_id, session_id)
  CHECK  status IN ('running','completed','failed','cancelled')
  CHECK  (status IN ('completed','failed','cancelled')) = (completed_at IS NOT NULL)
  CHECK  downstream_hold IS NULL OR (status = 'completed' AND downstream_hold = 'expectation_violated')
  CHECK  hold_released_by_change_id IS NULL OR downstream_hold IS NOT NULL
  CHECK  execution_policy ? 'v' AND (resolved_inputs IS NULL OR resolved_inputs ? 'v')
  TRIGGER identity, accepted_by_change_id, execution_policy, target_version immutable;
          on INSERT: accepted_by_change_id = graph_nodes.accepted_by_change_id, node live;
          a new row with execution_policy.legacy is rejected (legacy rows come only from the backfill);
          prompt/resolved_inputs/started_at immutable after start;
          a completed run's computation (prompt, resolved_inputs, outputs, notes) immutable,
            but superseded_at, downstream_hold and hold_released_by_change_id may each be set once;
          prompt required at start iff target_kind = 'agent'
  CONSTRAINT TRIGGER (deferred):
          status (and error) equal the projection of the run's attempts;
          every non-legacy run has its attempt 1 inserted in the same transaction

run_attempts
  UNIQUE (run_id, attempt_number)
  UNIQUE (submission_key) WHERE submission_key IS NOT NULL
  UNIQUE (run_id)     WHERE state IN ('preparing','submitted','running','unknown','suspended')  -- I9, every phase
  UNIQUE (session_id) WHERE state IN ('preparing','submitted','running','unknown','suspended')  -- current-phase constraint
  CHECK  state IN ('preparing','submitted','running','succeeded','failed',
                   'cancelled','interrupted','unknown','suspended')
  CHECK  (state IN ('succeeded','failed','cancelled','interrupted')) = (ended_at IS NOT NULL)
  CHECK  state NOT IN ('submitted','running','succeeded','suspended') OR dispatched_at IS NOT NULL
  CHECK  state <> 'failed' OR error ? 'code'
  CHECK  state <> 'suspended' OR (dispatch_kind = 'in_process' AND suspension ? 'kind')
  TRIGGER allowed transitions only (preparing→submitted|running|unknown|interrupted|cancelled|failed,
          submitted→running|unknown|…, unknown→any observed state,
          running→suspended, suspended→running|cancelled|failed);
          suspended→running only with session active, run current, and suspension.resume set for a gate;
          attempt_number = previous + 1 and previous attempt terminal;
          submission_key/request/dispatched_at set-once; cancel_requested_at one-way;
          terminal states immutable; no DELETE;
          new attempt rejected when the run is superseded or completed, or when
          attempt_number > execution_policy.max_attempts

domain_rows_enforce_producer (shared trigger, rewritten)
  producer = graph_nodes.target_name via the run (extended allow-lists on every result table;
             none on stored_files, which any run writes, or segmentation_pairs, owned through its batch);
  attached to: every table with created_by_run_id, segmentation_pairs, stored_files, node_runs, run_attempts,
               agent_checkpoints, agent_checkpoint_writes;
  no declared path (cubeai.attempt_id | cubeai.run_id | cubeai.actor) → reject;
  attempt path: attempt row locked FOR SHARE, of this run, in ('submitted','running');
                for the checkpoint tables: the run's latest attempt, in ('running','suspended');
  internal-job path: cubeai.run_id = this run, the run not completed, natural-key inserts only;
  user-gate path: gate columns and run_attempts.suspension.resume only, the run 'running' and current,
                  the attempt 'suspended' on that gate;
  harness path: node_runs/run_attempts lifecycle columns only; DELETE on the checkpoint tables
                only for runs terminal longer than the retention and named by no live seed_from;
  migration path: Alembic only;
  selection/approval columns additionally require the run 'running' and current (D25);
  execution bookkeeping allowed for any run that is not completed

training_jobs
  UNIQUE (task_name) WHERE ended_at IS NULL
  CHECK  (ended_at IS NULL) = (outcome IS NULL)

agent_checkpoints, agent_checkpoint_writes (3.2)
  PK     (run_id, checkpoint_ns, checkpoint_id) / (…, task_id, idx)
  FK     (run_id, session_id) → node_runs; attempt_id → run_attempts (same run)
  CHECK  metadata ? 'v' AND metadata ? 'spec_digest' AND metadata ? 'engine_version'
  TRIGGER rows immutable (no UPDATE); DELETE only on the harness purge path;
          a write to agent_checkpoint_writes is a no-op on a replayed key
```

**Indexes:**
- `node_runs (session_id) WHERE superseded_at IS NULL`;
- `node_runs (node_id, started_at)` (run history of a node);
- `run_attempts (session_id) WHERE state IN ('preparing','submitted','running','unknown','suspended')` (the unique index above);
- `agent_checkpoints (run_id, checkpoint_ns, created_at DESC)` (the latest checkpoint of a thread);
- `run_attempts (state) WHERE state = 'suspended'` (restart recovery and the gate-timeout sweep);
- `graph_changes (session_id, created_at)`;
- `graph_nodes (accepted_by_change_id)`;
- `chat_messages (chat_id, seq)`;
- `sessions (user_id, updated_at)`.

#### Transaction boundaries

Lock order: **session → graph → run → attempt → domain row**. Each transaction runs inside `publication()`, so its durable event commits with it. A domain write takes only the attempt lock (`FOR SHARE`) and then its row, which is a suffix of the same order. **No transaction stays open across an HTTP call to MONAI or Celery.**

| # | Transaction | Atomic effects |
|---|---|---|
| **T0 Create session** | Owner known | Insert `sessions(user_id)`, `session_graphs(version 0)` and its one `chats` row. Insert the opening `prompt` as the first `chat_messages` row and publish its `user_input` event |
| **T1 Propose** | Approval reasons present (in Plan mode, or a mandatory reason) | 1. Lock the session; refuse if it is `archived`.<br>2. Check the content: new `node_id`s unused, retained ids live. Compute the digest. Add the reasons the database derives (`PLAN_MODE`, `supersedes_completed`).<br>3. Mark any existing pending change `withdrawn` (or `expired` past the TTL), **then** insert the new `pending` change. A replayed idempotency key returns the existing row when the digests match, otherwise 409.<br>4. When the Main submitted it, insert its `tool_result` row (`{change_id, status:'pending', node_ids}`).<br>5. Publish `graph_change_proposed` + `approval_requested`.<br>Nothing executable changes. |
| **T2 Decide + apply** | Auto with no reasons, or a user decision quoting the digest | 1. Lock session → graph; refuse if `archived`.<br>2. Check ownership and `base_version = session_graphs.version`; otherwise the change goes `stale`. A pending change past the TTL goes `expired`.<br>3. Validate stable ids, one contiguous sequence, and that every `input_sources` entry names a live node earlier in the new sequence (the Validator has already checked the candidates and types). Recompute the derivable reasons: if applying the change would supersede a completed run and `supersedes_completed` is missing, or the session is in Plan mode and `PLAN_MODE` is missing, the change goes `stale` and is re-proposed.<br>4. Insert new nodes (assigning `node_seq`), update retained nodes **in place**, tombstone removals, reorder.<br>5. **Affected set** = changed, moved, rerun or removed nodes, plus every live node after the first of them in the new sequence.<br>6. Supersede the current runs of affected nodes that have one: `running` → `superseded_at` + active attempt `cancel_requested_at`; `completed`/`failed`/`cancelled` → `superseded_at`. Nodes that never started need nothing.<br>7. Set `accepted_by_change_id` = this change on added, edited and rerun nodes. For `release_hold_node_ids`, set `hold_released_by_change_id` on that node's current run. **No runs are created.**<br>8. `session_graphs.version += 1`; the change becomes `applied` (an Auto change is inserted directly as `applied`); any other pending change becomes `stale`.<br>9. When the Main submitted it, insert its `tool_result` row (`{change_id, status:'applied', node_ids}`).<br>10. Publish. |
| **T3 Accept input** | A user message, through the Agent System | Insert `chat_messages(message_id)` (a replay compares content; 409 if different), touch `chats.last_activity_at`, and publish durable `user_input {chat_id, message_id}`. Refuse if `archived`. The Agent System enqueues the message only after this commit returns. The frontend makes **one** call, to the Agent System |
| **T4 Start node / open attempt** | Executor, before dispatch | 1. Lock session → node (→ run).<br>2. Require: the node live; the session `active`; the previous live node's current run `completed` with no active `downstream_hold`; **no non-terminal attempt anywhere in the session**; and the node's `accepted_by_change_id` equal to what the Executor read (otherwise it re-reads the graph).<br>3. **First start (no current run):** check `resolved_inputs`: for every port, the pinned run is the current completed run of the selected live upstream node at resolution time (that node is earlier in the sequence and equals the `input_sources` entry when one exists, and the pinned port is in the run's `outputs`). Then insert the run as `running` with `accepted_by_change_id`, frozen `execution_policy` (including the session's `privacy_mode`), `target_version`, `resolved_inputs` and the prompt for agents, **plus attempt 1**, in the same transaction.<br>4. **Retry.** Allowed only when **all** of these hold:<br>• (a) the checks in step 2 pass;<br>• (b) the current run is `failed`, or it is `cancelled` and the request is an explicit user retry;<br>• (c) no attempt of the run is `preparing`, `submitted`, `running` or `unknown`;<br>• (d) the latest attempt is `failed` or `interrupted`, or `cancelled` under (b). A `cancelled` attempt is never retried automatically;<br>• (e) the latest `attempt_number` < `execution_policy.max_attempts`;<br>• (f) if the latest attempt was `interrupted` with `dispatched_at` set and `execution_policy.idempotent = false`, there is no automatic retry: the Recovery Agent or the user decides, and that explicit retry must still pass (a)–(e). **For a run with `execution_policy.resumable = true` (D27)**, "idempotent" is read from the run's latest checkpoint instead: the retry is automatic unless its `metadata.pending_tools` lists a tool with `idempotent = false`, because every finished tool call is stored and will not run again.<br>Then insert attempt `latest + 1` and set the run to `running`. A retry attempt of a resumable run continues the run's checkpoint thread.<br>6. **Seeded first start (D27):** when the accepting change set `seed_from` for this node, copy that checkpoint (with its pending writes) into the new run's thread as its first checkpoint, with `metadata.seeded_from`, and write attempt 1 as its writer, but only if it still exists, belongs to a run of the same node, and its `spec_digest` and `privacy_mode` match the new run. Otherwise start fresh and freeze `seed_from.applied = false` with the reason.<br>5. Attempts are inserted as `preparing` with `submission_key` and `request`; publish.<br>**Dispatch after commit:**<br>• `in_process`/`inline`: commit `running` + `dispatched_at` first, then spawn;<br>• `job`: send, then commit `submitted` + `dispatched_at`. The worker moves the attempt to `running` itself, from `preparing` or `submitted`, before it writes anything. |
| **T5 Reconcile** | Restart or lost acknowledgement | `suspended` attempts are **left unchanged**: nothing was running, and their state is in the checkpoint (D27). `in_process` and `inline` attempts that are not suspended → `interrupted` (the process that ran them is gone). Job attempts are queried by `submission_key`:<br>• running or queued → keep and re-attach;<br>• terminal → record the outcome;<br>• not found and `preparing` → resubmit the **same** key and request;<br>• not found and `submitted`/`running` → `interrupted` with `error.code='record_lost'`;<br>• backend unreachable → `unknown`, which blocks T4 for the whole session until a later reconcile succeeds.<br>Each terminal state recorded here is projected onto the run in the same transaction (`interrupted` → run `failed` with `error.code='interrupted'`, so T4 can retry it), and the attempt's `usage` is added to `tokens_used`.<br>**No attempt is created here.** |
| **T6 Complete / fail** | The attempt presents `attempt_id` | 1. Lock; require the attempt to be current, non-terminal and not `suspended` (a suspended attempt is resumed first, T10).<br>2. **Success:** validate `outputs` (refs against the table allow-list). Attempt `succeeded`; run `completed` + `outputs`. A superseded run stays non-current history. For a current run, evaluate the node's expectations **in the same transaction**: on a violation, set `downstream_hold='expectation_violated'` on **this run** and publish `expectation_violated`. If no live node follows, publish `graph_idle`.<br>3. **Failure:** attempt `failed` + `error`; run `failed` (projection).<br>4. Add `usage` to `tokens_used`.<br>5. The backend purges the job record and files only **after** this commit; their TTL starts at the purge. |
| **T7 Cancel / timeout / privacy switch** | User, Stop, T2, the Watchdog, or a switch to `phi_local` | **Request:** set the active attempt's `cancel_requested_at`, including when it is `preparing` or `unknown`. A Watchdog timeout also sets `error.code='timeout'`. A switch to `phi_local` requests cancellation of every non-terminal attempt whose policy says `standard`, in the same transaction as the settings change. A node that has not started has no run to cancel; stopping it is a `RemoveNode` change (allowed in Plan mode without approval).<br>**Confirm:** only once the task or job is observed stopped: attempt `failed` if its error is `timeout` or `gate_timeout`, otherwise `cancelled`; then run `failed` or `cancelled` unless it was superseded. Never inferred from a timeout alone. **A `suspended` attempt is confirmed in the same transaction as the request**, because it has no running work (D27) |
| **T10 Suspend / gate decision / resume** (3.2, D27) | The Node Runner (suspend, resume), the user (gate decision) | **Suspend** (harness path): lock session → run → attempt; require the attempt `running` and current, and the checkpoint named in `suspension.checkpoint_id` stored by this attempt. Set `suspended` + `suspension`; publish `run_attempt_suspended` (and `node_status` with `waiting_for_user` for a gate).<br>**Gate decision** (user gate path, in the same transaction as the domain gate columns when the gate has them): require the run `running` and current, and the attempt `suspended` on that gate with no `resume` yet; set `suspension.resume = {decision, comment, decided_at}`; publish `gate_resolved`, which wakes the Executor, not the Main.<br>**Resume** (harness path): require the session `active`, the run current, and for a gate `suspension.resume` set; set `running` + `resumed_at`; publish `run_attempt_resumed`. It is the **same** attempt (I8); no attempt is created here. |
| **T8 Inbox advance** | Main, after handling | `inbox_handled_through_event_id = n` only if every waking event ≤ n has been handled, and only after the reply and changes have committed. Add the turn's Main, Composer and Recovery token usage to `tokens_used` |
| **T9 Training** | Training route | 1. **Commit:** receipt + `workflow.frozen_at`.<br>2. POST outside any transaction.<br>3. **Commit:** `submitted_at`.<br>Reconciler: 404 → resubmit the same id. Cancel: record the intent, DELETE, and set `ended_at` only on a terminal report. Unfreeze only when no active receipt remains. |

---

### P.9 Migration plan

**Step 0: verify the deployed baseline.** Check the live Alembic revision (`n1f0a2c3d4e5`), row counts, running sessions and in-flight MONAI jobs. Stop the stack for both cut-overs, and measure backfill sizes rather than assuming them.

**No dual path (D26).** The AutoGen execution path is not kept working against the new schema; the roadmap already freezes it and deletes it. Rows it wrote stay as history.

#### Cut-over 1: migration `o1f0a2c3d4e5_v1_o_renames` + the mechanical code rename, in one commit

The migration is renames only, with no behaviour change:
1. `ALTER TABLE … RENAME` for the eight tables and `RENAME COLUMN` for their columns and for the kept tables' columns (FKs, CHECKs and index expressions follow automatically).
2. Rename every constraint and index to its `NAMING_CONVENTION` name.
3. Re-create every trigger function whose body mentions a renamed table or column, because PL/pgSQL bodies are text and do not follow renames: run, cardinality, step, producer, and the row rules of cohorts, segmentation batches and pairs, radiomics features, feature sets, model results, reports, report images and stored files.
4. Rewrite the JSONB key `correlation_run_id` → `batch_correlation_id` in `segmentation_batches.config`.
5. Rewrite `stored_files.retention_class` values `user_retained` → `retained` and `system_render` → `render`. Drop the `retention_class` and `retention_shape` CHECKs first, and re-create them after.
6. Historical `session_events.payload` stays untouched.
7. `downgrade()` reverses every rename. It is safe, because nothing else changed.

The same commit renames the code mechanically, with no behaviour edits:
- ORM classes, schemas, repositories, routes and file names;
- `backend_client.py` and every Agent System service and tool that passes `agent_run_id`/`step_id`;
- the `node_status` event; envelope version 2 (`node_id`, `run_id`, `attempt_id`);
- the frontend routes and fields (training UI, `ChatSidebar.tsx`);
- `CLAUDE.md` and `cubeai-docs/`.

A static source scan in the deterministic suite fails on any retired name outside migrations.

#### Cut-over 2: migration `p1f0a2c3d4e5_v1_p_session_graph` (additive) + the harness persistence code, in one release

All steps use the new names.

1. **Users.** Create `users` with one explicit `legacy` user. Add `sessions.user_id`, backfill it to `legacy`, then set `NOT NULL`.
2. **Sessions:**
   - add `execution_mode` (default `auto`; existing rows `auto`), `privacy_mode`, `tokens_used`, `inbox_handled_through_event_id`;
   - `control_state`: `pausing`/`paused` → `paused`; `archived` stays; every other value → `active`;
   - narrow the CHECK. The code that stops writing progress statuses ships in the same release.
3. **`session_graphs.version`:** backfill `1` where `approved_at IS NOT NULL`, else `0`.
4. **`graph_changes`:**
   - **Approved plans:** one change per approved plan, with `author='legacy'`, `base_version=0`, `applied_version=1`, `status='applied'`, `decided_by='user'`, `decided_at=approved_at` and `idempotency_key='initial-backfill'`. Its `content` (with `v`) is the ordered node snapshot, using the existing `step_id`s as node ids.
   - **Draft plans** (steps but never approved) **never become an applied change.** Each one becomes a `pending` change with:
     - `author='legacy'`, `base_version=0`, `idempotency_key='draft-backfill'`, `content.origin='legacy_draft'`, and the draft steps as content, keeping their ids;
     - `created_at` = the draft's last update time, and `decided_by`, `decided_at` and `applied_version` NULL;
     - `approval_reasons = {'approval_required:legacy_draft'}`, so only a user decision can ever apply it, never Auto mode.
     - It follows the normal TTL: if nobody decides, it becomes `expired` (or `withdrawn` when a new proposal replaces it), and it stays in history either way. The draft step rows are then deleted: they have no runs, nothing references them, and their full definitions now live in the change content, so no provenance is lost. If the user approves the change, T2 inserts the nodes with the same ids. This keeps I5 (no node without an applied change) and creates no fake history: the only `applied` legacy changes are those of plans that have `approved_at`.
   - `content_digest` is computed with the same canonical JSON function the API uses.
5. **`graph_nodes`:**
   - add `target_kind` (`'agent'`), `target_version` (`'legacy'`), `expectations`, `removed_at`, `node_seq` (backfill = `position`);
   - swap `uq_plan_steps_session_id` for the deferrable EXCLUDE;
   - replace the frozen-after-approval trigger with the change-guarded rule;
   - add `accepted_by_change_id` (backfill change #1, then `NOT NULL` + FK).
6. **`node_runs`:**
   - add `accepted_by_change_id` (backfill change #1, then `NOT NULL` + FK);
   - add `execution_policy` (backfill `{"v":1,"max_attempts":1,"legacy":true,"privacy_mode":"standard"}`), `target_version` (`'legacy'`), `superseded_at`, `outputs`, `downstream_hold`, `hold_released_by_change_id`;
   - **`outputs` backfill for completed runs, only from an exact mapping. Lineage is never guessed.** A port is backfilled only when all of these hold:
     - (a) the run's `target_name` fixes the port and its table: population → `cohort` in `cohorts`; segmentation → `masks` in `segmentation_batches`; feature extraction → `features` in `feature_sets`; `modeling_agent` → `modeling` in `model_results`; result reporter → `summary` (self); LaTeX reporter → `report` in `reports`;
     - (b) every row taken has `created_by_run_id` = this run;
     - (c) for a type whose ref names **one** row, the run produced exactly one such row; for a type whose ref is a **list** (`ModelingOutput`), the list is all of the run's rows in that table.
     - If any condition fails, that port is left out; if no port qualifies, `outputs` stays NULL. A backfilled map carries `origin: 'legacy_backfill'`. A run without the port a later node needs cannot be pinned: the Validator reports `MISSING_INPUT`, and the Main reruns that node. A NULL is always preferred to a lineage that might be wrong;
   - **never-started legacy runs** (`pending`) → `cancelled` **as migration cleanup, not as a user cancellation**:
     - `superseded_at` and `completed_at` = the migration time;
     - `error = {code:'legacy_not_started', source:'migration'}`;
     - no `cancel_requested_at` and no attempt, because nothing ever ran, so nothing was stopped;
     - no `cancelled` event is published, and every report or metric of user cancellations filters on `error.source`.
     - The node's next start creates a fresh run;
   - drop `UNIQUE(node_id)` and add the partial unique index;
   - narrow the status CHECK to `running|completed|failed|cancelled` (after step 7 maps `running`);
   - replace the row-rules trigger, and **drop** the cardinality trigger, because runs are created lazily.
7. **`run_attempts`:** backfill **only what is provable**. For each started run, insert attempt 1 with `dispatch_kind='legacy'`, a NULL `submission_key`, `started_at = run.started_at`, `dispatched_at = run.started_at`, and a state mapped from the run: `running` → `interrupted` (the stack was stopped), `completed` → `succeeded`, `failed` → `failed`. Earlier retries were never recorded, so none are invented. A run whose attempt becomes `interrupted` is set to `failed` with `error.code='interrupted'`. Its legacy policy (`max_attempts: 1`) means it is rerun, not retried.

   **7a. Agent checkpoints (3.2, D27):** create `agent_checkpoints` and `agent_checkpoint_writes` with their trigger, add `run_attempts.suspension` and `resumed_at`, and include `suspended` in the state CHECK and both active-attempt indexes. No backfill: legacy runs have no checkpoints and no `resumable` policy, so a legacy run is never resumed, only rerun.
8. **Producer trigger** rewritten: extended allow-lists, the D24 fencing with `FOR SHARE`, and the D25 selection rules. Every Application API write route sets `cubeai.attempt_id` (or `cubeai.run_id` for internal jobs) with `SET LOCAL` before it writes.
9. **`training_jobs`** created with no backfill.
10. **`chats`:** existing data already has one chat per session: migration `d1f0a2c3d4e5_v1_d_chats_split` created exactly one per session, and the frontend writes only to the one `get_chats_for_session` returns first. A second chat can appear only from a race between `resolveChatId` calls (it is then empty) or a direct API call. No persisted message is `role='system'` or a `context_summary`. The step therefore does not merge anything:
   - delete every chat that has no messages while another chat of the same session exists;
   - if a session still has more than one chat, **stop the migration** with an error that lists those session ids and the query to inspect their messages. The person running it decides which chat to keep; the migration never guesses or merges conversations;
   - add `UNIQUE (session_id)`. Together with `POST /chats/session/{id}` returning the existing chat, this closes the race for good.
11. **`chat_messages`:** an identity column added to a filled table is numbered in physical order, not by time, so `seq` is built in four steps:
   - add `seq` as a plain `bigint`;
   - backfill it with `row_number() OVER (ORDER BY created_at, message_id)`;
   - set `NOT NULL`;
   - `ALTER COLUMN seq ADD GENERATED ALWAYS AS IDENTITY (START WITH max+1)`.
   - Then add `UNIQUE (seq)` and the `(chat_id, seq)` index, and widen the `role` CHECK with `tool`. The repositories then order by `seq` (`repositories/chat_messages.py:87,121`).
12. **`downgrade()`** refuses when any node has more than one run, any run has more than one attempt, any run was started after the cut-over, any draft was moved into a change, any non-legacy user or change exists, any agent checkpoint or `suspended` attempt exists, or any `tool` message or `training_jobs` row exists (the o1 schema cannot hold them). Otherwise it restores the o1 rows, and rebuilds each `active` session's progress value from its restored runs: `running` if a run is running, else `failed` if one failed, else `completed` if the plan is approved and every node completed, else `ready`. Only the transient `planning` and `pausing` do not come back.

**Application API code in the same release:**
1. **Ownership.** A `current_user` dependency (the configured default user until authentication exists) and a session filter on every session-scoped route. The Agent System's `/ws` handshake and `/input` check ownership through the Application API.
2. **Input (T3).** The frontend calls the Agent System's `/input` once; the Agent System calls T3 and enqueues after the commit. The Agent System also sweeps for waking events above the cursor.
3. **Graph and runs.**
   - T0–T8 replace `approve_plan`/`create_pending_runs`. The old `/plans/…/approve` route returns `410 Gone`.
   - Fencing is **mandatory** for every attempt.
4. **Status.** Remove the Agent System's progress writes (`session_service.py:385-386`) and serve the derived activity.
5. **Pinned resolution.**
   - `resolve_run_for_target`, `get_population_cohort` and the segmentation-review "latest batch" lookup resolve from the calling run's `resolved_inputs`.
   - "Current" is used only in T4, when pinning.
   - Contract test: *two runs on one node; a consumer started against run 1 still reads run 1*.
6. **Training (T9).** The training route (today `workflow.py:516-548`) plus a startup reconciler.

#### Phase 3: execution-adapter prerequisites

1. **MONAI terminal re-queue.** An explicit re-queue of a terminal job under the same id with an incremented internal attempt, releasing the capacity slot on any error, with tests. Until this lands, a failed segmentation pair cannot be re-executed within the same run.
2. **Job platform (Celery + Redis).**
   - `submission_key = {job_id}.{attempt_number}` is both the Celery task id and the key of the Redis job record (one record per attempt).
   - Redis runs with **AOF persistence**.
   - A terminal record's TTL starts only when the harness purges it after T6, so "record not found" means "never submitted" for `preparing`, and "lost" (`record_lost`) for anything later.

#### Phase 4: harness rollout

- The Main writes `graph_changes`. The Composer and the Recovery Agent draft proposals that the Main submits.
- The Executor runs the live sequence by `position`.
- Plan/Auto uses `execution_mode`, and PHI-Local uses `privacy_mode`.
- When AutoGen is deleted: drop `sessions.intent` and `session_graphs.approved_at`.

---

### P.10 Deliberately not stored now

| Item | Why | Add when |
|---|---|---|
| `node_connections`, edge JSON | Sequential execution only; `position` + the input rule (D23) express every dependency | True DAG execution: follow the five steps in §P.5 *No `node_connections` table* |
| `run_artifacts`, generic artifact metadata (availability, producer attempt, manifest) | Typed rows + `outputs` + pinned `resolved_inputs` cover a sequential typed pipeline | Untyped files flow between nodes, or attempt-scoped checkpoints appear |
| `graph_nodes.hold_reason` (start-time holds) | Approval reasons are known at proposal time (D7); expectation holds live on the run that caused them; budget and resource refusals are runtime (D19). One column with three writers could release one kind of hold while clearing another | A start-time condition appears that needs a *human* decision and cannot be known at proposal time |
| Checkpoints for **tool** nodes | Agent nodes are checkpointed since 3.2 (D27). Tool nodes are inline or jobs; segmentation resumes per pair on domain rows, and job attempts re-attach by `submission_key` | The first resumable tool node that is not a job |
| A generic `approvals` table | Durable approvals of work are `graph_changes`; approvals inside a node are gates on the suspended attempt (D27) | — |
| Checkpoints for the Main, the Composer and the Recovery Agent | The Main is rebuilt from the chat (D21); the Composer and the Recovery Agent are short agent-as-tool calls with a hard timeout | A Main turn costs minutes or many tokens (see *Main turn record* below) |
| A LangGraph `BaseStore` (cross-session agent memory) | Not needed now, and memory across sessions needs its own PHI rules | Agents need durable memory across sessions: an `HttpStore` over a new Application API table, with a guardrail that keeps PHI out |
| Resource grants, pool tables | Celery and MONAI own capacity; the Executor's semaphores are runtime | The harness owns durable reservations |
| Controller lease / fence table | "One non-terminal attempt per session" + attempt fencing prevent duplicate execution and stale writes | More than one controller is deliberately deployed |
| Fingerprint, reuse link, reuse-policy column | Sequential suffix invalidation keeps the unaffected prefix; `execution_policy.fresh` covers fresh reruns | Cross-version caching |
| Context summary on `sessions` | A system message in the chat keeps the compaction boundary inside the transcript | — |
| Many chats per session (`requested_in_chat_id`, per-chat context and compaction, routing events and approvals to a chat) | The session has one graph, one Main and one inbox. A second conversation over the same graph would change it without seeing the first | Users need parallel conversations over one session: drop `UNIQUE (session_id)` on `chats` and add these three |
| Main turn record (`main_turns`: `turn_id`, `input_event_id`, state, tokens, model, `started_at`, `ended_at`) | The transcript already records every call: the `tool_calls` row is committed before the tool runs, the call carries `started_at`, and the `tool_result` row carries `status`, `ended_at` and `error`. What the UI can derive is limited: a `user_input` event after `inbox_handled_through_event_id` means only that the input is **not yet handled**. It may be queued, the Main may have crashed, or it may be working, so the UI shows "Processing…". A call without a result means it started and has not finished; it is running only while the Agent System is alive, otherwise it is closed as `interrupted`. "Calling tool X now" comes from an ephemeral live event, never from stored state. A restart re-handles the input, which is cheap for a short turn, and every research write is a node. Turn token usage goes to `tokens_used` in T8 | When any of these becomes true: (1) a turn costs minutes or many tokens, so it must resume rather than restart; (2) per-turn metrics (tokens, model, latency, cost) are needed; (3) more than one Agent System worker runs and a turn needs a claim. Then add a `main_turns` table, **never runs**: a run needs a graph node and the Executor (I3, I5), and in Plan mode a node needs approval, so every read would need one. A direct tool that writes is not a reason for this table: by D21 it becomes a node |
| Raw backend status / receipts | The backend owns raw state; `state` + `error` hold the confirmed outcome | — |
| Event sequence, dedupe key, FKs, wake flag on `session_events` | See §P.6 | — |
| User roles, teams, sharing, credentials | One owner per session | — |

---

### P.11 Risks and assumptions

**Tables that could still be merged, and why they are not:**
- **`run_attempts` into `node_runs`:** only possible if every node had exactly one try and no uncertain submission, which contradicts the Watchdog, the Recovery Agent and "unknown blocks dispatch".
- **`session_graphs` into `sessions`:** possible, but it gains nothing for correctness and costs FK and trigger rewrites.
- **`training_jobs` into columns on `workflow`:** possible if training history is declared out of scope.

**Derived or projected columns, each with one writer:**
- `node_runs.status` and `error` change only in the attempt-transition transaction, and a deferred trigger checks them against the attempts at commit.
- `session_graphs.version` equals `max(applied_version)` and is kept because it is the locked CAS token.
- `sessions.tokens_used` is the sum of attempt and turn usage, advanced only at attempt end and turn end.
- `outputs[port].phi` is a manifest snapshot. The Context Manager uses `stored OR manifest`, so it can only get stricter.

**Declared dual views:**
- Physical backend state vs `run_attempts.state`: SQL stores confirmed outcomes, and reconciliation always re-reads the backend first.
- Agent checkpoint vs domain rows (D27): the checkpoint holds what the agent *saw and said*; the domain rows and `outputs` hold what the node *produced*. A resumed agent re-reads results from the domain rows (for example a review decision), never from its own messages, and nothing downstream reads a checkpoint.
- `scratchpad_notes.status` is agent-reported and must be treated as non-authoritative.

**Candidates to trim if they prove unnecessary:**
- The deferred "run inserted with its attempt 1" check duplicates T4.
- `request` on attempts is needed only if a job request cannot be rebuilt byte-identically from `resolved_inputs`.

**Failure cases:**

| Scenario | Behaviour |
|---|---|
| Crash between T4 and dispatch | `in_process`: the attempt is already `running` with `dispatched_at`, so T5 marks it `interrupted` and rule (f) decides. `job`: T5 finds `preparing` and resubmits the same key only if the backend has no record; otherwise it re-attaches. Unreachable → `unknown`, which blocks the session |
| Timeout while the process is still alive | The attempt stays non-terminal with `cancel_requested_at` and `error.code='timeout'`. No new attempt can start (I9) until the stop is confirmed, so the old process never writes beside a new one |
| Write from an old attempt that is still alive | Rejected by the trigger: its `attempt_id` is not the run's active attempt (D24) |
| Write racing with T6/T7 | The trigger holds the attempt row `FOR SHARE`, so the terminal transition waits for the write, or the write sees the terminal state and fails |
| Two earlier nodes produce a compatible output for one input port (for example a training node and a refit node, both `ModelingOutput`) | The Validator returns `AMBIGUOUS_INPUT` with the candidates. The Main (or the user) names the source in `input_sources`; the harness never picks one |
| A change inserts a node that makes a later node's input ambiguous | The whole target sequence is validated, so the change is refused until the later node's `input_sources` is set in the same change |
| Superseded run still stopping when the next node is ready | The session-wide index blocks T4 until the old attempt is confirmed stopped. No duplicate GPU work, and the sequence stays sequential |
| Selection changed after completion | Refused (D25). A different choice is a `RerunNode`, which supersedes the downstream runs that pinned the old choice |
| Node inserted between a violating node and the next one | The hold is on the violating run, so the inserted node is not ready either. Only a `ReleaseHold` or a rerun of the violating node releases it |
| Restart in the middle of a Main turn | The input is handled again because the inbox cursor did not advance. Calls without a result are first closed with `status:'interrupted'`, so the new turn starts from a consistent transcript |
| Main crashes after applying a change, before T8 | The control call's `tool_result` committed with the change, so the re-handled turn sees that the work was already submitted. A literal replay is deduped by `idempotency_key = sha256(turn_id‖call_id)` |
| Notification to the Agent System lost | T3 is called by the Agent System itself, and the periodic sweep re-reads waking events above the cursor |
| The model reuses a `call_id` in a later turn | Accepted: results are unique per `(turn_id, call_id)` |
| The Main misreads "modify" vs "new" | A wrong modification that would supersede completed work needs a user decision (`supersedes_completed`), and the diff preview shows what reruns. A wrong "new" is only a proposal for a new session that the user can refuse |
| The caller omits a derivable approval reason | T1/T2 add `PLAN_MODE` and `supersedes_completed` themselves; T2 marks a change `stale` if they would be missing |
| Session switched to `phi_local` while an external-model run is active | T7 requests cancellation of every attempt whose frozen policy says `standard`, in the same transaction as the switch |
| Late success from a superseded run | Recorded as `completed` + `superseded_at`. Its rows are keyed to the old run, selection is blocked, and nothing pins it |
| Two proposals in one session at once (the Main and a user edit) | Serialized by the session lock. The second withdraws the first, and the Main tells the user in the chat. A harness check never proposes on its own, so it cannot withdraw a proposal under review |
| Auto change applied while a proposal is pending | T2 marks the pending change `stale` in the same transaction, so the user is never asked to approve a dead proposal |
| Two controllers running by mistake | Starting the same node twice is rejected by the partial unique index on current runs. Any second active attempt in the session is rejected by the session-wide index. In-process reconciliation still assumes a single worker |
| Graph edited while the Executor is about to start a node | T4 re-checks readiness and the node's `accepted_by_change_id` under the session lock. A changed node is re-read, so a run never executes a stale definition |
| AutoGen code runs against the new schema | Not supported (D26). Its writes fail the new CHECKs and fencing; the roadmap freezes and deletes it |
| Crash while an agent node's tool call is running (D27) | T5 marks the attempt `interrupted`. The retry attempt continues the checkpoint thread: finished tool calls are read back, only the pending call runs again, and T4 rule (f) blocks the automatic retry when that call is not idempotent |
| Restart while a node waits on a gate or a pause | Nothing to do: the attempt is `suspended`, holds no process, and T5 leaves it alone. It resumes when the decision arrives or the pause ends |
| Gate decided while the Agent System is down | T10 records the decision and publishes `gate_resolved`. On start-up the Executor finds the suspended attempt with `resume` set and resumes it |
| A superseded or cancelled attempt keeps writing checkpoints | Refused by the fencing trigger (latest attempt, `running` or `suspended` only); `HttpCheckpointSaver` raises `StaleAttempt` and the engine stops |
| `langgraph` upgraded while runs are suspended | The resume fails with `checkpoint_incompatible` (`engine_version` mismatch); the node is rerun. The upgrade procedure in `agent-system/agent-checkpoints.md` says to drain or accept this |
| A `seed_from` checkpoint was purged, or the agent changed | T4 starts the run fresh and freezes `seed_from.applied = false`; the result is correct, only slower |
| The user never decides a gate | The session stays `awaiting_user`; with `gate_timeout_s` set, the Watchdog cancels with `gate_timeout`. Stop or a rerun works at any time (T7 confirms a suspended attempt at once) |

**Assumptions:**
1. One Agent System worker, so `in_process` and `inline` attempts become `interrupted` on restart, except `suspended` ones, which hold no process (D27).
2. Redis uses AOF once job records are the receipts, and a record's TTL starts only after the harness purges it.
3. The MONAI terminal re-queue is fixed before failed pairs or jobs are retried.
4. Execution stays sequential. A DAG follows the five steps in §P.5.
5. A single default user is acceptable until authentication exists. **Multi-user must not be enabled before verified identity, because `user_id` is only as trustworthy as whatever supplies it.**
6. Detached subprocesses of tools die with the Agent System. Verify this per tool, or make such tools job tools. Otherwise a "stopped" in-process attempt may still have a live child writer, which only run-level fencing would catch.
7. Every write to a fenced table declares its path (D24). A route that forgets is rejected by the trigger, not silently accepted, and the contract test fails if a new table with `created_by_run_id` is created without the trigger.

---

### P.12 How reference harnesses persist the same concepts

| Concept | Reference | Comparison |
|---|---|---|
| Work item with an owner and an attempt count | Codex `agent_job_items.assigned_thread_id` + `attempt_count`: [codex-rs/state/migrations/0014_agent_jobs.sql#L18-L35](https://github.com/openai/codex/blob/5e96aabd68dc6194f5ad928cb7f5d0bc71b72df9/codex-rs/state/migrations/0014_agent_jobs.sql#L18-L35). Codex later dropped both tables: [0042_drop_agent_jobs.sql](https://github.com/openai/codex/blob/5e96aabd68dc6194f5ad928cb7f5d0bc71b72df9/codex-rs/state/migrations/0042_drop_agent_jobs.sql) | **Different.** Codex items have no external submission identity, so an owner column and a counter are enough. CUBE.AI job attempts do, so they get rows, and the owner check becomes the `attempt_id` fence. Since Codex removed these tables, they are a comparison, not proof that the pattern works in production |
| Per-thread settings and token usage | Codex `threads.approval_mode`, `sandbox_policy`, `tokens_used`: [codex-rs/state/migrations/0001_threads.sql#L1-L19](https://github.com/openai/codex/blob/5e96aabd68dc6194f5ad928cb7f5d0bc71b72df9/codex-rs/state/migrations/0001_threads.sql#L1-L19) | **Same idea** as `sessions.execution_mode` / `privacy_mode` / `tokens_used` |
| One conversation per session | Codex: a thread is one conversation with its own history; a new topic is a new thread, and branching is a fork: [codex-rs/core/src/thread_manager.rs#L175-L176](https://github.com/openai/codex/blob/cc31e374fc15cd616fca19c07d55f9fa7e6d7e31/codex-rs/core/src/thread_manager.rs#L175-L176). Claude Code: one append-only transcript per session: [docs/architecture.md#L202-L214](https://github.com/VILA-Lab/Dive-into-Claude-Code/blob/0f4ee62f09b25ea0343336cca8903c6db796abb0/docs/architecture.md#L202-L214) | **Same idea** as one `chats` row per session. Many Claude Code sessions sharing one project directory needed fixes for shared state ([CHANGELOG.md#L1763](https://github.com/anthropics/claude-code/blob/6160717d8994f236ab381cf534fc1cde5347c12f/CHANGELOG.md#L1763)), which is the risk D3 avoids |
| Tool calls paired by id; marker for a turn cut mid-way | Codex `ResponseItem::FunctionCall` / `FunctionCallOutput` with `call_id`: [codex-rs/protocol/src/models.rs#L1074-L1089](https://github.com/openai/codex/blob/cc31e374fc15cd616fca19c07d55f9fa7e6d7e31/codex-rs/protocol/src/models.rs#L1074-L1089); the `<turn_aborted>` marker: [codex-rs/core/src/thread_manager.rs#L204-L210](https://github.com/openai/codex/blob/cc31e374fc15cd616fca19c07d55f9fa7e6d7e31/codex-rs/core/src/thread_manager.rs#L204-L210) | **Same idea** as `tool_calls`/`tool_result` rows with `call_id`, and `status:'interrupted'`. The `call_id` comes from the model, which is why CUBE.AI scopes it to a turn. Claude Code fixed sessions looping on "unexpected tool_use_id" from calls and results that did not match: [CHANGELOG.md#L1026](https://github.com/anthropics/claude-code/blob/6160717d8994f236ab381cf534fc1cde5347c12f/CHANGELOG.md#L1026) |
| Compaction boundary inside the transcript; session grants not restored on resume; one writer per store | VILA-Lab analysis of Claude Code: [docs/architecture.md#L200-L220](https://github.com/VILA-Lab/Dive-into-Claude-Code/blob/0f4ee62f09b25ea0343336cca8903c6db796abb0/docs/architecture.md#L200-L220) | **Same rule, different store** (JSONL vs a system message in the chat). Supported not persisting in-run approvals as resumable state until revision 3.2; since D27 they are gates on a suspended attempt, because the agent node itself is now checkpointed, while the Main still follows this rule (D21). Its `flock()` multi-instance coordination is the file-system counterpart of the session-wide active-attempt index |


---

## Not in this roadmap

- **The frontend Surface:** chat `/skills` UI, a live Graph Canvas with diff preview, the Approvals Panel, and Settings for mode, privacy and models. The backend routes and events are ready for it.
- **The evaluation harness** (system §36).
- **system Phase 4 (Scaling):** embedding-based registry retrieval, domain sub-planners, a Tool Builder, the Result Verifier, tracing and metrics beyond run usage and timings.
- **`spawn_agent` / `wait_agent`**, and cross-session caching.
- **DAG or parallel execution.** The roadmap stays sequential; Appendix P §P.4 lists the current-phase constraints and §P.5 the five steps that open the DAG path without a rewrite.
- **Authentication and multi-user.** One configured default user owns every session until a verified identity exists (Appendix P D2, assumption 5).
- **Deployment and storage:** enabling the container or microVM runners in production, any real MCP server, and object storage (MinIO/S3); jobs keep the shared `/storage` mount.
- **Scaling the Agent System:** a multi-worker Agent System (it needs a shared inbox and executor ownership, e.g. in Redis). Agent checkpoints already live in the Application API (D27), so they need no change for it.
- **LangGraph `BaseStore`** (cross-session agent memory) and **LangGraph Server / Platform:** not used. A store would be an `HttpStore` over an Application API table with a PHI guardrail (Appendix P §P.10); LangGraph Server brings its own database and is ruled out by D1.
