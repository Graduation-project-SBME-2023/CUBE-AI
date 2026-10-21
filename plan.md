# CUBE.AI merged roadmap: Agent Harness + Tool Platform

> **How we work (2026-10-21):** five people in parallel, one area each, in one phase, with one checkpoint (the first working flow). Each plan names its **Owner**; see [team-plan.md](team-plan.md) for who does what and in which order.

## Context

This roadmap plans the next version of CUBE.AI: an agent harness (a Main Agent, a live Session Graph, an Executor, Runtime Guardrails) built on a framework-neutral tool platform (the Toolkit, tool packs, jobs). The persistence design is **Appendix P**, the only database specification; Plan 6 implements it. The work is Plans 0–17 (4 and 14 removed), each with phases, built by five people in parallel (`team-plan.md`).

**Decisions:**

- **`run_modeling` is a tool.** There is no `modeling_agent` (system_plan wins over tools_plan here).
- **The Claude Agent SDK (Python, `claude-agent-sdk`) is the agent engine** for every LLM agent: the Main Agent, the Composer, the Recovery Agent and every execution agent (decided 2026-10-19; it replaces LangGraph). It runs behind the existing `AgentEngine` protocol as `ClaudeAgentEngine` (Plan 9). The SDK supplies the agent loop, context management and compaction, hooks, subagents and saved sessions; CUBE.AI keeps the Session Graph, the Executor, the Diff Engine, the Validator, the Guardrails and every durable state. There is no LangGraph anywhere in the roadmap.
- **Agent nodes keep a saved SDK session per run** (decided 2026-10-19). The session id is the run id (`session_id = run_id`), and its transcript is stored through the Application API by a `SessionStore` adapter (`HttpSessionStore`, Appendix P D27, revision 3.4). A retry attempt resumes the same session (`resume = run_id`), so an agent node continues after a restart instead of reasoning again, and a finished tool call is never run again. An agent that has to ask the user (`ask_user`), a tool call that needs a mandatory approval, and a pause **suspend** the attempt; no process waits. Internal agents are **SDK subagents** (the literature review's 5 included); `run_agent` is removed. A rerun can start from a point of an earlier run (`seed_from`, through `fork_session` + `resume_session_at`). The Main keeps a saved SDK session too, one per CUBE.AI session (D21, revised 2026-10-20). The Composer and the Recovery Agent are SDK subagents of the Main.
- **Every model is reached through LiteLLM** (decided 2026-10-19). One `litellm` proxy service, configured from a file and with **no database**, exposes the Anthropic Messages API; the SDK points `ANTHROPIC_BASE_URL` at it. Model aliases route to Claude or to local models on Ollama. The Model Gateway maps a profile to a LiteLLM alias plus the client settings that alias needs (Plan 9.2). Only LiteLLM holds provider credentials.
- **Approvals come from what the user asks for** (decided 2026-10-19; Appendix P D28). There is no fixed in-node gate: the cohort approval and the segmentation review happen only when the user asked for them. From the user's words, the Main sets `approval: before | after` on the nodes it submits (the node does not start, or the next node does not start, until the user releases it), and every execution agent has an `ask_user` tool for when the user asked to be consulted inside a step. Plan / Auto modes and the Guardrails' mandatory approvals stay as they are.
- **No MCP edge for now** (decided 2026-10-20). Plan 14 (third-party MCP servers as untrusted tools) is removed; its number is kept. Every tool an agent can call is a CUBE.AI tool.
- **Use the SDK harness for everything it already does** (decided 2026-10-20). Nothing the Claude Agent SDK provides is rebuilt in CUBE.AI. Concretely: the Main keeps a **saved SDK session** and the SDK compacts it (no context rebuild from the chat, no own summaries); the **Composer and the Recovery Agent are SDK subagents** of the Main (no `plan` / `recover` agent-as-tool, no repair loop of ours); **skills are SDK skills** (`SKILL.md` in a local plugin, no loader of ours); **tool access** is the SDK's own (a session only has the tools its spec lists, so no visibility check of ours for SDK sessions); **limits** are `max_turns` / `max_budget_usd`; **free questions** to the user are the SDK's `AskUserQuestion`; **progress steps** are the SDK's `TaskCreate` / `TaskUpdate`; **PHI filtering of tool results** is a PostToolUse `updatedToolOutput`. CUBE.AI keeps only what the SDK does not do: the Session Graph, the Executor, the Validator and the Diff Engine, the database and its fencing, the job platform, the durable inbox, LiteLLM, the timeouts, and the domain tools. Before building anything around an agent, check the SDK docs first (*Global rules*).
- **Not used for planning:** `cubeai-docs/architecture/monai-agent-integration.md`. Segmentation stays the execution agent of Plan 3.
- **One phase, five areas, one checkpoint** (decided 2026-10-20, areas revised 2026-10-21; there is no second phase). Each person owns one area from start to end and builds its simple version first, so the **first working flow** (a question in the chat → the PDF) comes early: until it works, heavy tools run inline, there is no agent memory, no questions to the user, Auto mode only, and synthetic data only. See `team-plan.md`.
- The scope includes the **cut-over and AutoGen removal** (Plan 17). The evaluation harness is **not** in this roadmap. The frontend changes that a backend change forces are made by the person who makes the backend change (`team-plan.md`); a redesign of the frontend Surface is not in this roadmap.
- **Python 3.13 for every image we build** (Agent System, compute worker, Application API), done first in Plan 0. The MONAI Label image keeps its own Python 3.10.
- **PyCaret is removed.** Modeling runs on scikit-learn, with LightGBM, XGBoost and SHAP. The 5 PyCaret tools are rewritten on that engine and **renamed** in Plan 7 (the only rename besides `save_literature_output`).
- **Appendix P (the persistence design) is the only database specification.** Plan 6 implements it, and every other plan uses its names, its write paths (D24) and its transactions T0–T10.
- **`execution_mode` defaults to `auto`** (decided 2026-10-01). Plan mode is opt-in; the mandatory approval reasons apply in both modes.
- **Execution is sequential only** in this roadmap (D5). The rules that follow from it are listed as current-phase constraints (Appendix P §P.4), so DAG execution can be added later without a rewrite.
- **One vocabulary (D20).** The schema and the code are renamed once, in Phase 6.1, before Plan 2 moves the tools. Every plan after that uses the new names (`node_runs`, `run_id`, `graph_nodes`, `node_id`, `session_events`, `stored_files`, `segmentation_batches`, …).
- **No dual path (D26).** The AutoGen path is not kept working against the new schema; the rows it wrote stay as history.
- **Jobs run on PostgreSQL with Procrastinate** (decided 2026-10-09), not on Celery + Redis as tools_plan says: one database; only the Application API sends and cancels jobs; the queue rows are the physical job state and `run_attempts` the attempt outcome (D12). There is no sandbox (Plan 4 is removed): a job runs in its own child process, and stdio MCP servers are refused.

## Principles (from both documents)

1. **The model owns the strategy; the harness owns execution, state, permissions and safety.** There is no router, no intent classifier, no intake agent, and no rule that picks a strategy from complexity or step count.
2. **Orchestration is not execution.** The Main Agent and the Composer only _describe_ work. Only the Executor _runs_ it.
3. **Long-running work is asynchronous.** Submitting work returns `RunAccepted` immediately, and the Main Agent never waits for a node.
4. **The Session Graph is live.** It is versioned, can change during execution, and is never "finished".
5. **Guardrails decide boundaries, never reasoning.** Each check returns exactly one of `allowed | approval_required | permission_denied | invalid | resource_unavailable`.
6. **A tool is a callable action (`ToolSpec`).** An agent is a definition (`AgentSpec`) and never a tool. Delegation is a tool.
7. **Heavy compute runs as jobs on PostgreSQL (Procrastinate)** behind one `JobClient` contract. Only the Application API sends and cancels jobs. Each job runs in its own child process.
8. **The Application API owns all durable state, and the database enforces the invariants.** Graph changes, runs, attempts, approvals and fencing are rows and constraints (Appendix P), not conventions inside the Agent System.

## Decisions by topic

The decision in force on each topic the source documents covered.

| # | Topic | Merged decision |
|---|---|---|
| 1 | Package layout | `toolkit/`, `toolpacks/`, `agentpacks/`, `harness/`, `planning/`, `execution/` (*Target layout*); the forbidden package names are in the Global rules. |
| 2 | The word "harness" | "Harness" now means only the system_plan session layer. `RunHarness`, `node_resolver` and `DirectToolExecutor` move into `execution/node_runner.py` and `catalog.resolve()`. |
| 3 | Tool metadata | One `ToolSpec` with both sets of fields. Derived fields and consistency rules are checked by `catalog.validate()` (Phase 1.1). |
| 4 | Who may call what | `visibility` is a **set** of `main`, `graph`, `agent` and `harness`. `planner` is renamed `graph` (it may become a Tool Node). Main may call a tool directly only if it is `inline` and not `heavy`. Everything else runs as a node. |
| 5 | Delegation | Main has **no** delegation tool. It uses `execute` / `graph_apply`, which create an Agent Node and return `RunAccepted`. An execution agent reaches its internal agents as **SDK subagents** (the SDK's `Agent` tool, inside a node that is already tracked); `run_agent` is removed (decided 2026-10-19, Plan 9.6). The Composer and the Recovery Agent are SDK subagents of the Main (decided 2026-10-20): they propose with `graph_propose` and never execute anything. |
| 6 | Modeling | `run_modeling` is a job tool (manifest from system §15). `modeling_agent` is not created, and its prompt becomes the `modeling` skill. PyCaret is removed: the 5 modeling tools are rewritten on a scikit-learn engine, renamed, and stay graph-visible job tools (Plan 7). |
| 7 | Agent engine | `ClaudeAgentEngine` (Claude Agent SDK, decided 2026-10-19) implements `AgentEngine` for **every** LLM agent: the Main, the Composer, the Recovery Agent and the execution agents. The SDK is the loop of one agent; the inbox loop, Executor, Diff Engine and Validator stay plain code, because the live graph changes while it runs and must survive restarts, which an agent loop does not own. **Agent nodes keep a saved SDK session per run**: `HttpSessionStore` stores the transcript through the Application API (D1 holds; Appendix P D27), with `session_id = run_id`, so every attempt of a run resumes the same session and a rerun starts fresh. `ask_user`, mandatory tool approvals and pause suspend the attempt (T10). The Main keeps one saved SDK session per CUBE.AI session, compacted by the SDK (D21, revised 2026-10-20); the Composer and the Recovery Agent are its subagents. |
| 8 | Runs | _Persistence:_ `agent_runs` is renamed `node_runs` and holds every node run, agent or tool. A run is created lazily when its node starts, together with attempt 1 (D8); every try is a row of the new `run_attempts` table (D9). Domain FKs become `created_by_run_id`. The job id is `sha256(run_id\|tool\|args)`, and the submission key adds `.{attempt_number}`. |
| 9 | Plan contract | This contract is dropped. The Validator and the registry decide what is valid. A contract test checks the producer allowlists in the Application API against the catalog. _Persistence:_ `plans` and `plan_steps` are renamed `session_graphs` and `graph_nodes` and become the live graph (D4, D6); every mutation is a `graph_changes` row (D7). |
| 10 | Session status | _Persistence:_ `sessions.status` is renamed `control_state` and holds only intent: `active \| paused \| archived` (D13). The activity (`idle`, `running`, `pausing`, `awaiting_approval`, `blocked`) is derived and never stored. Nothing is ever `completed`. |
| 11 | Resources | `resources` is the manifest field. `resource_class` is derived from it: `cpu_heavy`→`compute.cpu`, `gpu`→`compute.gpu`, `highmem`→`compute.highmem`; these are the Procrastinate queue names (Plan 5), and each queue's worker concurrency is the real limit. Executor pools are set in `config/resources.yaml` and are runtime only: a node waiting for a pool, or refused at start, is not stored; it is published as `node_start_refused` (D19). |
| 12 | Cache vs job dedupe | _Persistence:_ only the **job id** now. It re-attaches an attempt to the same run after a restart. Fingerprints and cross-version caching are deferred (Appendix P §P.10): sequential suffix invalidation keeps the unaffected prefix, and `execution_policy.fresh` covers fresh reruns. |
| 13 | Human gates | _Persistence:_ every durable approval of work is a `graph_changes` row decided by a user who quotes its digest (D7); there is no `approvals` table. **There is no fixed gate inside a node** (decided 2026-10-19, D28): approvals happen where the user asked for them. A node's `approval: before` stops its start until the user releases it, and `approval: after` puts a `user_review` hold on its completed run; both are released by a user `ReleaseHold`. Inside a node, the agent asks with `ask_user`, which **suspends** the attempt through the SDK's `defer` (T10): the wait is durable, survives a restart and holds no process. A mandatory tool approval inside a node (Phase 11.3) suspends the same way. The cohort approval and the segmentation review stay as domain forms on their rows (D24 `user gate` path), used only when the user asked; otherwise the cohort is frozen and the masks are accepted automatically (`auto`). |
| 14 | PHI | `phi` is derived from `data_scopes`. Artifacts carry a `phi` label. The Model Gateway filters the LiteLLM aliases by locality (every model a session's CLI may call, the background model included). `gpt-oss:120b-cloud` counts as **external**. |
| 15 | Events | `source` = the node's ref name (an agent type or a tool name), or `main_agent` for the Main Agent. _Persistence:_ the durable types of Appendix P (`graph_change_*`, `node_status`, `run_attempt_*` (including `run_attempt_suspended/resumed`), `gate_resolved`, `expectation_violated`, `node_start_refused`, `graph_idle`, `session_state`, `approval_requested`) are registered in `event_registry.py`, and `pipeline_agent_status` is renamed `node_status` in Phase 6.1. |
| 16 | Internal agents | Internal agents stay `AgentSpec`s (`kind: internal`). An execution agent reaches them as **SDK subagents** (`AgentDefinition`s built from their specs), and their transcripts are stored with the parent's under a `subpath`. The segmentation module, which calls its two internal agents from code, calls the engine (`AgentEngine.run`, a one-shot call) instead of AutoGen `AssistantAgent`. The 6 literature `FunctionTool`s become registered tools. The literature review is an **ordinary execution agent** (decided 2026-10-19): its 5 internal agents are optional subagents, and nothing fixes their order. |
| 17 | Runtime | Python 3.13 for our images (Plan 0). PyCaret removed; modeling on scikit-learn (Plan 7). MONAI stays on its own Python 3.10 image. |
| 18 | Persistence | Appendix P (decisions D1–D27) is the database design. Plan 6 implements it in two cut-overs (D26). |
| 19 | Execution shape | Sequential only (D5): the order is `graph_nodes.position`; inputs come from the strict D23 rule plus an optional `input_sources`; there is no edge table. A DAG later follows Appendix P §P.5 *No `node_connections` table*. |
| 20 | Agent state and fan-out inside a node | _Your decision (2026-10-19, replacing the LangGraph one of 2026-10-02):_ agent-node reasoning state is the run's SDK transcript, stored by the Application API (D27); a finished tool call is in the transcript, so a resume never runs it again. Long work inside one tool records each finished item in its domain rows and continues from them on a re-call (cohort labels per patient, segmentation pairs). Parallel work inside a node is the SDK's own: read-only tools run concurrently, and an agent may run several subagents at once (literature search and ranking). This is parallelism **inside** a node, like the segmentation fan-out (D10), so the graph stays sequential (D5). |

## Global rules (apply to every plan)

- **AutoGen is frozen until Plan 17.** No effort goes into these files, and they may break:
  - `agents/execution_agents/*_agent.py`, `agents/planning_agents/`, `agents/literature_review_agent/literature_review_agent.py`, `agents/run_bound_agent.py`;
  - `teams/`, `api/runners.py`, `services/pipeline_monitor.py`, `task_manager/`, `system_prompts/`.

  The segmentation domain module (`agents/execution_agents/segmentation/`) is **not** frozen; it is domain code.

- **Only the new work is tested, between the checkpoints.** The project does not need to run after each plan or each phase of a plan. It must run end to end at the first working flow (`team-plan.md`), and again when every part is complete.
- **No MCP tool is renamed**, and no tool's success result shape changes. The only renames are `save_output` → `save_literature_output` (Plan 2) and the 5 `pycaret_*` tools (Plan 7); their success result shapes still do not change. Agent ids stay the same. For example, `study_population_planner_agent` keeps its id, and "Population Agent" is only its display name.
- **Python 3.13.** New code targets Python 3.13. Code that also runs in the MONAI image (`Backend/storage_paths.py` and every file `Dockerfile.backend.gpu` copies) must stay compatible with Python 3.10.
- **No PyCaret.** No new code imports `pycaret`, and no new code depends on PyCaret objects or PyCaret setup arguments.
- **Forbidden package names:** `platform`, `core`, `tools`, `mcp` and `types`, and no regular package named `config`.
- **Only the Application API gets `DATABASE_URL`.** All graph, change, run, attempt, agent-transcript and result state goes through its HTTP API, in the transactions T0–T10 of Appendix P §P.8. The one exception is the compute workers' `JOBS_DATABASE_URL`, a role limited to the Procrastinate objects (Plan 5); a job's child process never gets it.
- **Agent transcripts go through the Application API only.** The one `SessionStore` is `execution/session_store.py` (`HttpSessionStore`); the SDK's example adapters (Postgres, Redis, S3) and `InMemorySessionStore` are never used outside tests. A transcript is reasoning state, never a result: results are typed rows and `node_runs.outputs` (D27). Nothing parses the transcript entries except the SDK itself: their format is internal to the SDK and changes between versions, so reads go through the SDK's store helpers (`get_session_messages_from_store()` and friends).
- **Use the SDK first.** Before building anything around an agent (its loop, context, memory, compaction, subagents, skills, questions to the user, permissions, limits, progress, usage), check the Claude Agent SDK docs. If the SDK does it, use it; CUBE.AI code only configures it. A plan item that rebuilds an SDK feature is a bug in the plan.
- **The Claude Agent SDK is imported only in `toolkit/engines/` and `execution/session_store.py`.** Every SDK session runs with `strict_mcp_config=True`, `setting_sources=[]`, the built-in subagents off, and CUBE.AI tools served by an in-process MCP server whose handlers call `dispatch.invoke`. Of the SDK's built-in tools, only these are on, and only where Phase 9.4 says: `Agent` (subagents), `Skill` (the `cubeai` skills), `AskUserQuestion` (free questions from an execution agent), `TaskCreate` / `TaskUpdate` (progress steps). Every other built-in tool is in `disallowed_tools`. The CLI's own files (`CLAUDE_CONFIG_DIR`, the working directory) are resolved through `storage_paths`; nothing is written to `~/.claude`.
- **Every model call goes through LiteLLM** (`ANTHROPIC_BASE_URL` = the `litellm` service). No Agent System code holds a provider key or calls a provider directly.
- **Every write on the execution path declares its D24 path** (`attempt`, `internal job`, `user gate`, `harness` or `migration`). The database rejects a write that declares none.
- **Names after Phase 6.1** follow the vocabulary of Appendix P §P.2. Old names appear only in migrations, in old durable events, and in the problem catalog, which describes today's code. Phase 6.1 renames identifiers inside tool code, never tool names, tool argument names or success result shapes.
- **Generated files** are resolved through `Backend/storage_paths.py`. Graph or inbox state is never written to workspace files, and the write guard is respected.
- **Run exactly one Agent System worker.** Main loops, executors and inboxes are process-local asyncio objects. Everything durable lives in the Application API's PostgreSQL, the job queue included.
- **Tests** use synthetic data. Unit and contract tests use `FakeAgentEngine`, Procrastinate's `InMemoryConnector` and in-memory backends, and never touch the network. The engine's own tests (Plan 9) run the real SDK against a scripted fake Anthropic Messages server on `127.0.0.1` (the CLI is a subprocess, so `respx` cannot reach it); they need the bundled CLI and carry the `claude_cli` marker.
- **Strategy-free harness:** no harness rule may read the request text or count steps to choose a strategy. A unit test scans the guardrail rules for this.

## Architecture after the merge

```
CONTROL PLANE  (agent_system/harness, agent_system/planning)
  User -> POST /session/{id}/input -> Session Inbox -> Main Loop (main_agent: one ClaudeSDKClient per SessionRuntime,
                                                                  saved SDK session resumed by session id, SDK compaction, D21)
     Main's tools (in-process MCP server; knows every catalog entry from the index, calls only these):
                    main-visible light tools (dispatch.invoke, inline)
                    control tools: execute, graph_apply, graph_propose (-> Validator), graph_inspect,
                                   registry_search/describe, node_cancel, node_message, artifact_read
     Main's SDK subagents (the Agent tool): composer, recovery_agent (read-only tools + graph_propose; never apply)
     SDK skills (the Skill tool, cubeai plugin); graph summary added to every turn by a UserPromptSubmit hook
     every action -> PreToolUse hook -> Runtime Guardrails -> allowed | approval_required | permission_denied | invalid | resource_unavailable
                                    |
                                    |  graph change (full target sequence, base_version; approval: before|after per node) -> RunAccepted {node_id, change_id, status}
                                    v
EXECUTION PLANE  (agent_system/execution)
  Diff Engine --T1/T2--> Session State Store (Application API, Appendix P: session_graphs, graph_changes,
                         graph_nodes, node_runs, run_attempts, agent_transcript_entries, typed domain tables, session_events)
  Executor -> Node Runner -+-> Agent Node: AgentSpec on ClaudeAgentEngine (one SDK session per run, session_id = run_id;
                           |               internal agents as SDK subagents; tools through dispatch.invoke)
                           |     HttpSessionStore --transcript (session_id = run_id)--> Application API (agent_transcript_entries, D27)
                           |     ask_user / tool approval -> PreToolUse defer -> attempt suspended (T10) -> resume = run_id on the answer
                           |     pause -> interrupt at the next tool boundary -> suspended (T10) -> resume with a "continue" message
                           +-> Tool Node:  dispatch.invoke -> inline
                                                          -> job      (JobClient -> Application API -> Procrastinate (PostgreSQL) -> compute worker -> child process)
  Watchdog (heartbeats, timeouts, OOM, interrupted; suspended attempts hold no process and are not watched)
  every start / finish / cancel / reconcile / suspend / resume is a transaction of the Application API (T4 / T6 / T7 / T5 / T10)
  important events (failure, expectation violated, graph idle, approval decided, a node waiting for the user's release) -> Session Inbox
  answers to a suspended attempt (gate_resolved) -> Executor (resumes the node; the Main is not woken)

MODELS
  every SDK session (CLI subprocess) --ANTHROPIC_BASE_URL--> litellm (config file, no DB) --> Claude | Ollama (local)
```

## Target layout

```
Backend/
  agent_system/
    toolkit/                      Plan 1 ✅ (no AutoGen / Claude Agent SDK imports, except engines/)
      manifest.py registry.py ports.py context.py events.py results.py catalog.py
      tools/spec.py tools/consistency.py tools/registry.py
      agents/spec.py agents/registry.py agents/engine.py agents/loader.py (Plan 3)
      artifacts/base.py artifacts/builtin.py                    (system_plan "types/artifacts.py")
      runtime/dispatch.py runtime/inline.py runtime/cancel.py runtime/session_sink.py
      runtime/job.py (Plan 5)
      (delegation/ removed: run_agent in Plan 9.6, agent_as_tool in Plan 12.5; subagents are the SDK's)
      engines/claude_engine.py engines/options.py engines/mcp_bridge.py engines/hooks.py   (Plan 9)
    toolpacks/                    Plans 2, 7, 9, 12 (discovered automatically)
      common/ population/ db_reads/ segmentation/ reporting/ bookkeeping/ literature/   (Plan 2)
      modeling/ (incl. run_modeling; engine/ on scikit-learn) radiomics/                  (Plan 7)
      interaction/ (ask_user)                                                             (Plan 9)
      control/                                                                            (Plans 12, 13)
    agentpacks/
      core/main_agent/agent.md, core/{composer,recovery_agent}/agent.md (Main's subagents) (Plan 3)
      research/<agent_type>/agent.md (+ hooks.py)                                         (Plan 3)
    skills/cubeai/                (an SDK plugin: skills/<name>/SKILL.md, nothing else)    (Plan 3)
    harness/                      Plans 9, 11, 12, 13
      model_gateway.py guardrails.py approvals.py inbox.py turn_context.py
      skills_index.py main_loop.py session_runtime.py privacy.py
    planning/                     Plans 8, 12: graph_model.py validator.py inputs.py proposals.py
    execution/                    Plans 6, 8, 9, 10, 13
      graph_store.py affected.py diff_engine.py executor.py node_runner.py
      resources.py expectations.py watchdog.py recovery.py session_store.py (Plan 9)
    config/                       data only: models.yaml (9) resources.yaml (10)
  application_api/                Plan 6: migrations "o" (renames), "p" (session graph) and "t" (agent transcripts, user approvals; Phase 6.6); models, repositories, schemas, routes (Appendix P); Plan 5: migration "s" (job queue), the job dispatch, cancel and read routes
  jobs_core/                      Plan 5 (backends/app_api.py; backends/monai_http.py: Plan 15)
  compute_worker/                 Plan 5 (Procrastinate app: supervisor, child, periodic tasks)
deploy/
  litellm/config.yaml             Plan 9.2 (model aliases; no database)
```

## Milestones, order and dependencies

| # | Plan | Milestone | Depends on | Status |
|---|---|---|---|---|
| 0 | Runtime and cleanup: Python 3.13, PyCaret removed, dead code removed | M0 Runtime | – | 🟡 0.1 implemented, CI not green yet; 0.2 (remove what the SDK replaces) not started |
| 1 | Toolkit Core | M1 Toolkit | – | ✅ Done (1.1–1.5) |
| 2 | Tool Packs | M1 | 1 | ✅ Done (2.1–2.5) |
| 3 | Agent Definitions and Skills | M1 | 1, 2 | 🟡 Built on the pre-SDK design, not merged; Phase 3.0 reworks it for the SDK |
| 4 | _(removed: no sandbox)_ | – | – | – |
| 5 | Job Platform (PostgreSQL + Procrastinate) | M2 Compute | 1, 6 | Not started |
| 6 | Session State Store: Persistence Design (Application API) | M3 Harness | – (6.1 right after 0) | ✅ 6.1–6.5 done; 6.6 (agent transcripts, user approvals; decision 2026-10-19) not started |
| 7 | Modeling Engine, Heavy Tools as Jobs + `run_modeling` | M2 | 0, 2, 5, 6 | 🟡 7.0–7.2 done (engine, rename, legacy records); jobs wait for Plan 5 |
| 8 | Validator and Diff Engine | M3 | 1, 6 | ✅ 8.1–8.4 done (`ce5a777e`, `3ac5411c`); the 2026-10-19 follow-up (`seed_from`, `approval`) not started |
| 9 | Claude Agent SDK Engine, Session Store and Model Gateway (LiteLLM) | M3 | 1, 3, 6 (6.6 for 9.5) | Not started |
| 10 | Executor, Node Runner, Watchdog | M3 | 5, 6, 8, 9 | Not started |
| 11 | Runtime Guardrails, Modes, Approvals | M3 | 5, 6, 8, 10 | Not started |
| 12 | Session Layer: Inbox, Main Loop, Context, Skills, Planner | M3 | 8, 9, 10, 11 | Not started |
| 13 | Recovery and Privacy | M4 Hardening | 10, 11, 12 | Not started |
| 14 | _(removed: no MCP edge for now, 2026-10-20)_ | – | – | – |
| 15 | MONAI Jobs + Segmentation Client | M5 Job fixes | 5 (Phase 5.1 only); the review of 15.4 needs Phases 9.3 (`ask_user`) and 6.6 (`auto` review values) | Not started |
| 16 | Other Job Systems | M5 | 5, 15 | Not started |
| 17 | Cut-over, AutoGen Removal, Cleanup, Docs | M6 | all | Not started |

**Implementation order:** 0 → 1 ✅ → 6.1 ✅ → 2 → 3 → 5 → 6.2–6.5 → 7 → 8 → 6.6 → 9 → 10 → 11 → 12 → 13 → 15 → 16 → 17.

This is the dependency order. Since 2026-10-20 the five areas of `team-plan.md` are built in parallel against contracts and fakes, so it does not say who works when.

## The team

How the remaining work is split between five people (the five areas and their order, the first working flow, the contracts, the working rules and the folder owners) is in [team-plan.md](team-plan.md). In short:

| Person | Area | Plans and phases, in order |
|---|---|---|
| **Person 1** | Compute jobs | Plan 5 → Plan 7 (7.1, 7.3, 7.4, 7.5) → Phases 16.1, 16.3, 16.4 |
| **Person 2** | Agents, subagents and segmentation | Phase 3.0 (rework of the built Plan 3) → Phases 15.1–15.3 → Phase 9.6 → Phases 9.7 + 15.4 → `ask_user` (Phase 9.3 item 3) → Phase 16.2 → Phase 13.1 |
| **Person 3** | Execution and session state | Phase 6.6 → Plan 10 → Phase 11.3 → Phase 13.2 |
| **Person 4** | Agent engine and models | Phase 9.0 spike → Plan 0 → Phases 9.1–9.4 → Plan 8 follow-up → Phase 9.5 → Phase 13.3 |
| **Person 5** | Main Agent and session layer | Plan 12 → Phases 11.1, 11.2 → Phase 17.1 |
| **Everyone** | Cut-over | Plan 17.2–17.6, each for their own area |

---

---

# Plan 0 — Runtime and cleanup: Python 3.13, PyCaret removed, dead code removed

**Owner:** Person 4 (Agent engine and models). See [team-plan.md](team-plan.md).

**Goal:** every image we build runs Python 3.13, PyCaret and its old pins are gone, and the rest of the roadmap is written and tested on 3.13. The MONAI Label image is not touched. Then the built code loses what the Claude Agent SDK and the new harness replace (Phase 0.2), so no later plan builds on it.

## Phase 0.1 — Runtime (status)

Built (2026-10-06): the agents image is `python:3.13.16-slim` with PyRadiomics pinned to `@8ed5793` and no PyCaret; `requirements.txt` (still UTF-16 LE with CRLF) has `numpy>=2.3,<3`, `shap`, `lightgbm`, `xgboost` and `joblib`. The Application API image is `python:3.13-slim`. `Backend/tests/runtime/test_image_smoke.py` (marker `docker`) checks the image inside `deterministic-tests`. The MONAI image stays on Python 3.10. `mcp` is pinned `>=1.17,<2` until AutoGen is deleted (its `autogen_ext` import needs it). Known limit: the `LBP3D` image type produces no features (SciPy 1.17 removed `sph_harm`); the default types are not affected.

**What is left:** `backend-tests.yml` passes; then Phase 0.1 is done.

## Phase 0.2 — Remove what the SDK and the new harness replace

Code built in Plans 1, 2 and 8 that the Claude Agent SDK (or the Node Runner and Node inputs) now does. Nothing new depends on it, and the frozen AutoGen path cannot run its tools anyway, so it is deleted first, with its tests. Removals that need a replacement to exist first are listed where the replacement is built (*Removed later*, below).

1. **`toolkit/` (Plan 1):**
   - `delegation/run_agent.py` and `delegation/agent_as_tool.py`, their tests, and the `run_agent` tool: delegation is the SDK's subagents (Phases 9.6, 12.5); the `delegation/` package goes;
   - `ToolSpec.for_caller` and the binding in `tools/registry.py` (only `run_agent` used it);
   - `ToolContext.depth` (the SDK limits subagent depth with `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`); `for_subagent()` keeps only the `subagent` tag;
   - `ToolContext.mailbox` (no user: a message to a running agent is the SDK's streaming input, Phase 12.2 `node_message`);
   - `AgentSpec.human_in_the_loop` (questions are `AskUserQuestion` and the `ask_user` forms, Phase 9.3);
   - in `catalog.py`: `CONTROL_FORBIDDEN_TOOLS` (`run_agent`, `plan`, `recover`) and `IndexAudience.COMPOSER` (the Composer is the Main's subagent and uses the Main's index). The comments that say internal agents are reached through `run_agent` now say SDK subagents.
2. **`planning/graph_model.py` (Plan 8):** `RecoveryProposal` (the Recovery Agent answers in text with an optional `proposal_id`, Phase 13.1).
3. **`toolpacks/bookkeeping/` (Plan 2):**
   - `scratchpad_register_agent` and `scratchpad_set_status`: a run's lifecycle is the Node Runner's and T4–T6's;
   - `scratchpad_read_agent`, `scratchpad_read_report`, `read_agent_output`, `read_all_agent_outputs`: an agent gets upstream results from its **Node inputs**; the reporter's prompt text in `reporting/template_tools.py` that names `read_all_agent_outputs` is updated;
   - `read_agent_task_context`: the task and the inputs are the first message of the run (`context_builder`), and then they are in the agent's saved SDK session.
4. **Tests:** the deleted tools leave the Definition of Ready table; the catalog counts and `test_boundary.py` follow.

**Removed later, where their replacement is built:**

| What | Removed in | Replaced by |
|---|---|---|
| `write_agent_output` (and its view hook branch) | Phase 10.1 | the SDK's structured output (`output_format`), written by the Node Runner at T6, which also shows the agent's view and clears the run's temp dirs |
| `scratchpad_write_note` | Phase 12.7 | the SDK's `TaskCreate` / `TaskUpdate`, shown on the canvas as `agent_task` events (the execution view, `views/execution/utils.ts`, reads them instead of the notes) |
| `scratchpad_write_summary`, `scratchpad_raise_flag` | Phase 3.2 (decided there) | the agent's `output_contract`, if its summary and flags become part of its structured output |
| `common/llm.py` on `client_config` | Phase 9.7 | the Model Gateway's `extraction` profile through LiteLLM |
| `client_config.py` provider settings, `/client-config` keys and URLs | Phase 9.2 | LiteLLM aliases per profile |
| `agent_checkpoints` tables, routes, repositories; the Main's tool-call contract in `chat_messages`; `main_idempotency_key(turn_id, call_id)` | Phase 6.6 (migration "t") | the SDK's transcripts (`agent_transcript_entries`, `main_transcript_entries`) and `sha256(session_id‖tool_use_id)` |
| `apply(…, turn_id, call_id)` in the Diff Engine | Plan 8 follow-up | `apply(…, session_id, tool_use_id)` |

**Done when:** the deterministic gate passes, the catalog loads with the tools above gone, and no source file names a removed symbol.

---

# Plan 1 — Toolkit Core ✅

**Goal:** a framework-neutral core for tools, agents, artifact types, context, events, dispatch and delegation.

## What exists (read the code in `Backend/agent_system/toolkit/`)

- **Contracts:** `manifest.py` (the manifest enums), `tools/spec.py` (`ToolSpec`: identity, handler, input model, input and output ports, the manifest fields `execution`, `side_effects`, `cost`, `resources`, `trust`, `isolation`, `data_scopes`, `visibility`, `timeout_s`, and hooks such as `on_result`), `tools/consistency.py` (`check_manifest()`), `ports.py`, `agents/spec.py` (`AgentSpec`), `context.py` (`ToolContext`: `session_id`, `run_id`, `attempt_id`, `node_id`, and live fields left out of `to_payload()`, `cancel` among them), `events.py`, `results.py` (`error_result`, `RunAccepted {node_id, change_id, status}`).
- **Artifact types** (`artifacts/builtin.py`): `Cohort`, `ImageSet`, `MaskSet`, `FeatureTable`, `FeatureImportance`, `ModelingOutput`, `LiteratureContext`, `ResultSummary`, `ReportDocument`.
- **Registries and catalog:** `registry.py` (a generic `Registry[T]`), `tools/registry.py` (`@tool`), `agents/registry.py`, `catalog.py` (`build()`, `validate()`, `resolve()`, `index(audience)`, `describe()`). `build()` walks the packs and re-raises a broken import; it refuses a control agent with `delegates` (Phase 3.4 lifts this for `main_agent`, whose subagents are the Composer and the Recovery Agent).
- **Dispatch** (`runtime/dispatch.py`), the one way to run a tool: `Dispatcher(before_invoke=…, runners={Execution.INLINE: run_inline})`; the job runner plugs in through `runners` (Plan 5). A `before_invoke` hook returns `None` to let the call through, or a value that answers instead of the tool (a refusal or an approval request). The budget is `timeout_s + queue_timeout_s`. Dispatch sends `tool_started` / `tool_finished` / `error` for every call. `runtime/session_sink.py` has `SessionEventSink(session, manager)`.
- **Delegation:** `delegation/agent_as_tool.py` and `delegation/run_agent.py` exist and are both removed (Phases 9.6 and 12.5): delegation is the SDK's subagents.
- **Engine protocol:** `agents/engine.py` (`AgentEngine`, `FakeAgentEngine`); Phase 9.3 extends it.
- **Tests:** `tests/support/toolkit_fixtures.py` (`call_tool`, `make_context`), loaded from `tests/unit/toolkit/conftest.py` (the root conftest also runs in the Python 3.10 MONAI image). `test_boundary.py` keeps AutoGen and the Claude Agent SDK out of `toolkit/`, except `engines/`.

---

# Plan 2 — Tool Packs: move every tool and make the light tools ready ✅

**Goal:** `Backend/mcp/` is gone. All tools live in `agent_system/toolpacks/<pack>/` and are registered with `@tool` plus a full manifest. They read identity from the context, and the light tools pass the Definition of Ready.

## What exists (read the code in `Backend/agent_system/toolpacks/`; conventions in `CLAUDE.md`, *Tool packs*)

- Every tool is in `toolpacks/<pack>/` (`population`, `db_reads`, `segmentation`, `reporting`, `bookkeeping`, `literature`, `modeling`, `radiomics`), registered with `@tool` and a full manifest. `common/` holds the shared helpers and registers no tools. Packs import the toolkit only through `toolpacks/_toolkit.py`: a pack that imports the other import root drops out of the catalog.
- `common/manifests.py`: `READ_ONLY`, `WRITES`, and `INTERIM_HEAVY` (`cost="medium"`, `execution="inline"`), which marks the heavy tools until they become jobs: the 2 radiomics tools, the 5 modeling tools, `run_segmentation` and `run_segmentation_with_guard`.
- Identity comes from the context (`common/domain_api`: `session_id()`, `run_id()`, `attempt_id()`, `node_id()`, `agent_type()`). Every Application API write sends `X-Attempt-Id`, else `X-Run-Id` for a job. `DomainApiError` and `error_result_from()` map failures to `unavailable`, `not_found`, `conflict`, `bad_request` or `upstream_error`.
- A tool sends only `tool_log` / `tool_progress` (`common/emit.py`); its view updates come from its `on_result` hook (`common/views.py`).
- `run_segmentation_with_guard(cohort_id)` takes the `Cohort` port and returns a `MaskSet`. It cannot run until the segmentation module uses the engine (Phase 9.7): `SegmentationModule.initialize()` still starts the deleted `Backend/mcp/server.py`.
- The literature tools keep per-run state in `literature/run_state.py` (`use_pico(run_id, pico)`, `release(run_id)`); the literature agent's hooks call them (Plan 3).
- `tests/unit/toolpacks/light/test_definition_of_ready.py` checks every light tool; tool tests use `fake_domain_api` (respx) and `call_tool`.

**Left for later plans:** move `common/polling.py` to `jobs_core/polling.py` (Phase 5.1); call `aclose_shared_client()` from the app lifespan (Phase 17.1); move `targets_matcher._announced_sessions` and `literature/run_state.py` to the runtime that owns per-run state (Plans 10–12).

---

# Plan 3 — Agent Definitions and Skills

**Owner:** Person 2 (Agents, subagents and segmentation). Person 5 reviews `main_agent` and `composer`. See [team-plan.md](team-plan.md).

**Goal:** every agent is an `agent.md` file that becomes an `AgentSpec`, and the skills exist. Plan 9 makes the agents run.

**Status (2026-10-21):** Plan 3 is **built but not merged**, and it was built on the design that came **before** the Claude Agent SDK decision (2026-10-19): the LangGraph-era Plan 3 (checkpoints, in-node gates, a fixed `workflow` graph for the literature review, control-kind Composer and Recovery Agent with output contracts, and `skills/*.md` with `applies_to`). Phases 3.1–3.5 below are the **target**. Phase 3.0 lists what changes in the built code to reach it. Only after Phase 3.0 does Plan 3 merge.

## Phase 3.0 — Rework the built Plan 3 for the Claude Agent SDK

Do this on the built branch, before it merges. It is the same kind of change as Phase 0.2: delete what the SDK now does, add what the SDK needs, and change the meaning of the fields that stay. Phases 3.1–3.5 are the reference for every item.

**1. Removed:**

| What (in the built code) | Where | Why (replaced by) |
|---|---|---|
| The `gates` front-matter field, its known-gate list, and the "only `execution` agents have gates" rule | loader, `AgentSpec`, `catalog.validate()` | There is no fixed gate inside a node (D28). The cohort approval and the segmentation review are `ask_user(form=…)`, used only when the user asked (Phase 9.3) |
| `human_in_the_loop` (front matter and the derived field) | loader, `AgentSpec` (Phase 0.2 item 1) | `AskUserQuestion` and the `ask_user` forms |
| The `workflow` field, `hooks.build_graph`, and the rules "`workflow` or `delegates`, never both" and "a `workflow` agent has `build_graph`" | loader, `hooks.py`, `catalog.validate()` | The literature review is an ordinary agent; its 5 internal agents are optional SDK subagents in any order (Phase 9.6) |
| `max_context_tokens` as a required compaction threshold | loader | The SDK compacts by itself. Keep the field only if the Phase 9.0 spike finds a per-session setting |
| Every mention of `HttpCheckpointSaver`, checkpoints, the `compact` node, LangGraph or `StateGraph` | `agent.md` prompts, docstrings, tests | Saved SDK sessions (`HttpSessionStore`, Phase 9.5) |
| `gates: [cohort_approval]` on `study_population_planner_agent`, `gates: [segmentation_review]` on `segmentation_agent`, and every fixed "wait for approval" step in their prompts | `agentpacks/research/` | The prompt rule of Phase 3.2: ask only when the node instructions say the user wants it |
| `kind: control` on `composer` and `recovery_agent` | `agentpacks/core/` | They are `kind: internal`, `parent: main_agent`: the Main's SDK subagents (Phase 3.4) |
| `output_contract = GraphProposal` on `composer`, `output_contract = RecoveryProposal` on `recovery_agent`, and `RecoveryProposal` in `planning/graph_model.py` | `agentpacks/core/`, `planning/graph_model.py` (Phase 0.2 item 2) | Both propose with `graph_propose` and end with a `proposal_id` and a short text |
| "Except `plan` and `recover`" in the Composer's and Recovery Agent's tools, and every mention of the `plan` / `recover` tools in the Main's prompt | `agentpacks/core/` | There are no `plan` / `recover` tools: the Main asks its subagents with the SDK's `Agent` tool |
| `skills/*.md` with `applies_to`, and the skills index | `agent_system/skills/`, tests | SDK skills in a local plugin, listed per agent (Phase 3.5) |
| Any prompt line that names a tool removed in Phase 0.2 (`scratchpad_register_agent`, `scratchpad_set_status`, `scratchpad_read_agent`, `scratchpad_read_report`, `read_agent_output`, `read_all_agent_outputs`, `read_agent_task_context`, `run_agent`) | `agent.md` prompts, `allowed_tools` | The Node Runner owns the lifecycle; upstream data is in **Node inputs** |
| The LangGraph-native tests (gates, `workflow`, `build_graph`, derived `human_in_the_loop`, the skills index) | `tests/unit/agentpacks/` | The SDK-field tests below |

**2. Added:**

| What | Where | Phase |
|---|---|---|
| Front-matter fields `effort`, `skills`, `max_budget_usd` (all optional) and `gate_timeout_s` (default `null`), validated by the Pydantic model and carried on `AgentSpec` | loader, `toolkit/agents/spec.py` | 3.1 |
| Clear refusals for the old fields: `gates`, `workflow`, `human_in_the_loop` in a front matter fail with a message that names the replacement | loader | 3.1 |
| Refusals for implicit tools: `ask_user`, `AskUserQuestion`, `TaskCreate`, `TaskUpdate` listed in `allowed_tools`; `delegates` on an internal agent (depth 1); `delegates` on a control agent other than `main_agent` | loader, `catalog.validate()` | 3.1, 3.3, 3.4 |
| The new prompt rules in every execution agent: ask only when the node instructions say so (`AskUserQuestion` for a question, `ask_user` for the cohort or mask form); record main steps with `TaskCreate` / `TaskUpdate`; read upstream data from **Node inputs** | `agentpacks/research/*/agent.md` | 3.2 |
| The decision on `scratchpad_write_summary` and `scratchpad_raise_flag`: part of `output_contract` or not (Phase 0.2, *Removed later*) | `agentpacks/research/`, `toolpacks/bookkeeping/` | 3.2 |
| `literature_review_agent` as an ordinary agent: first version with no `delegates` and one prompt over its 6 tools; its `context_builder` (`use_pico`) and output hook (`release`, the review record, `lit_review_output`) | `agentpacks/research/literature_review_agent/` | 3.2 |
| The 5 literature internal agents rewritten as optional subagents: a `description` that tells the parent when to use each, and `search_executor` / `relevance_ranker` prompts written for one source / one paper so the parent can run several at once | `agentpacks/research/literature_review_agent/…` | 3.3 |
| `main_agent`: `delegates: [composer, recovery_agent]`, `skills: [research_study, modeling]`, the compact catalog index (`catalog.index("main")`) in its prompt, "ask the `composer` subagent and wait for its answer", and the approval wishes (D28) | `agentpacks/core/main_agent/agent.md` | 3.4 |
| `composer` and `recovery_agent` as internal agents: the read-only `main` tools plus `graph_propose`; the Composer's "propose, read the Validator errors, propose again" loop in its prompt | `agentpacks/core/` | 3.4 |
| The `cubeai` SDK plugin: `.claude-plugin/plugin.json` and `skills/<name>/SKILL.md` for the 5 skills, with `name` and `description` only | `agent_system/skills/cubeai/` | 3.5 |
| The SDK-field tests and the skill tests listed under *Tests* below | `tests/unit/agentpacks/` | 3.1–3.5 |

**3. Kept, with a new meaning:**

- `resumable`: same defaults per kind, but it now means a saved SDK session per run (`session_id = run_id`), not a checkpoint thread.
- `delegates`: now the agent's SDK subagents, not `run_agent` targets.
- `max_tool_iterations`: becomes the SDK's `max_turns`.
- `gate_timeout_s`: counts how long a **suspended attempt** (a question, a form or a tool approval) may wait, not a gate.
- The prompts copied from `system_prompts/system_messages.py`, the ports, `completion_check`, `context_builder`, `allowed_tools_fn` and the output-port `extract` hooks stay as built.

**Done when:** no file under `agentpacks/`, `skills/`, `toolkit/agents/` or `tests/unit/agentpacks/` names a removed field, tool or LangGraph symbol; the catalog has the agent counts of Phase 3.5's *Done when* (1 control, 6 execution, 9 internal); `validate()` passes; and the Plan 3 tests pass. Then Plan 3 merges.

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
   resumable: true
   gate_timeout_s: null
   effort: high
   ---
   <system prompt>
   ```
   **Claude Agent SDK fields** (decision 2026-10-19, used by Plan 9):
   - `resumable` (default `true` for `execution`, `false` for `control` and `internal`): the node keeps a saved SDK session (`session_id = run_id`, `HttpSessionStore`), and a retry attempt resumes it (D27);
   - `gate_timeout_s` (default `null`, no limit): how long a suspended attempt (a question, a form or a tool approval) may wait before the Watchdog cancels it. It is separate from `timeout_s`, which counts only running time;
   - `effort` (optional): the SDK's reasoning effort for this agent; the profile's default otherwise;
   - `max_context_tokens` (optional): the compaction threshold, kept only if the Phase 9.0 spike finds a per-session setting; otherwise the SDK's automatic compaction applies;
   - `max_tool_iterations` becomes the SDK's `max_turns`; `max_budget_usd` (optional) becomes the SDK's `max_budget_usd` for one run;
   - `skills` (optional, default none): the SDK skills this agent may load, from the `cubeai` plugin (Phase 3.5);
   - every `execution` agent gets the SDK's `AskUserQuestion` (free questions) and `TaskCreate` / `TaskUpdate` (progress steps) implicitly; an agent that owns a domain form (population, segmentation) also gets `ask_user` (Phase 9.3). None of them is listed in `allowed_tools`;
   - `delegates` are the agent's SDK subagents (Phase 9.6); there is no `workflow` field and no `gates` field (decision 2026-10-19: approvals come from what the user asks for, D28). `human_in_the_loop` is removed from `AgentSpec`.
2. **Hooks:** an optional `hooks.py` next to the file exports `completion_check`, `context_builder`, `allowed_tools_fn` and the output-port `extract` functions.
3. **`toolkit/agents/loader.py`:** reads the front matter with `yaml.safe_load`, validates it with a Pydantic model, attaches the hooks and returns an `AgentSpec`. `catalog.build()` calls it for every `agent.md` file under `agentpacks/`.

## Phase 3.2 — Execution agents (6)

| Agent | Source (copy from) | Inputs | Outputs | Notes |
|---|---|---|---|---|
| `study_population_planner_agent` | `population_agent.py:128-222` | – (params: criteria text) | `cohort: Cohort` | Conditional `extract_cohort_labels` via `allowed_tools_fn`; `completion_check` = `services/population_handoff.py:34-78`, 2 retries; freezes its cohort before it finishes: through `ask_user(form="cohort_approval")` when the user asked to approve it, otherwise automatically (D28) |
| `segmentation_agent` | `segmentation_agent.py:156-272` | `cohort: Cohort` | `masks: MaskSet` | The 2 segmentation internal agents are its subagents; asks for a review with `ask_user(form="segmentation_review")` only when the user asked for one, otherwise the masks are accepted automatically (D28) |
| `feature_extraction_agent` | `feature_extraction_agent.py:47-132` | `cohort: Cohort`, `masks: MaskSet` | `features: FeatureTable`, `importance: FeatureImportance` (optional) | Calls the radiomics job tools |
| `result_reporter_agent` | `result_reporter_agent.py:52-106` | `modeling: ModelingOutput`, `features: FeatureTable` (optional) | `summary: ResultSummary` | Uses its real id (not `"result_agent"`) |
| `latex_reporter_agent` | `reporter_agent.py:49-105` | `summary: ResultSummary`, `literature: LiteratureContext` (optional) | `report: ReportDocument` | |
| `literature_review_agent` | `literature_review_agent.py` | – (params: question / PICO) | `literature: LiteratureContext` | An ordinary agent: its 5 internal agents are optional subagents (`delegates`), and nothing fixes their order (Phase 9.6). **Its first version runs with no `delegates`** (the subagents follow): one prompt covering the PICO, the queries, the search, the ranking and the compiling, over its 6 tools. Its `context_builder` calls `literature.run_state.use_pico(run_id, pico)`; its output hook calls `release(run_id)`, records the review, and sends `lit_review_output` (today only the frozen `planning_agents/planner.py:1022` sends it), so `LiteratureReviewView` keeps working |

**Prompt work** (L14, plus the move to the graph): copy each prompt from `system_prompts/system_messages.py`, which stays frozen, into its `agent.md`. Then:

- remove the `agent_id=` arguments, and every tool that Phase 0.2 removes (`scratchpad_read_agent`, `read_agent_output`, `read_all_agent_outputs`, …);
- remove the register / set_status instructions (the Node Runner owns them);
- use one segmentation tool name;
- read upstream data from the **"Node inputs"** section that the Node Runner provides, not by reading the scratchpad by agent id;
- remove every fixed wait for an approval; say instead: "if the node instructions say the user wants to approve or be consulted, ask at that point" (`AskUserQuestion` for a question, `ask_user` for the cohort or mask form; D28);
- say that the agent records its main steps with `TaskCreate` / `TaskUpdate`, which the canvas shows as progress;
- **decide here:** if an agent's summary and its warnings become part of its `output_contract` (the SDK's structured output), `scratchpad_write_summary` and `scratchpad_raise_flag` are deleted in the same change, and the canvas reads the summary and the warnings from `outputs` (Phase 0.2, *Removed later*).

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

  All are copied from `literature_review_agent.py:1528-1560`. The parent lists them under `delegates`; they are its SDK subagents (Phase 9.6), and the parent decides whether and when to use each one. `search_executor` and `relevance_ranker` keep a prompt written for one source or one paper, so the parent can run several of them at once.

- **Internal agents become `AgentDefinition`s** (Phase 9.6): their `description` tells the parent when to use them, their prompt is the subagent's system prompt, and their `allowed_tools` are the subagent's tools.
- **`catalog.validate()` additions:** an internal agent's tools are visible to its parent; an internal agent has no `delegates` (subagent depth 1); `ask_user`, `AskUserQuestion` and the task tools are never listed explicitly.

## Phase 3.4 — The Main Agent and its two subagents (in `agentpacks/core/`)

1. **`main_agent`** (`kind: control`):
   - The prompt states system §1, §5 and §21: decide, never wait, answer directly when that is enough, submit work with `execute` / `graph_apply`, and ask the `composer` subagent for a plan whenever it helps (and wait for its answer). **No rule forces the planner.**
   - It also says how to carry the user's approval wishes into the graph (D28): "approve after every step" → `approval: after` on each node it submits; "ask me before each step" → `approval: before`; "ask me about X inside a step" → say so in that node's `params.task`; no wish → no approval. It is guidance, not a harness rule.
   - `allowed_tools_fn` = all tools with `main` visibility (what it may **call**). What it may **use as a node** comes from the compact index in its system prompt (`catalog.index("main")`, every entry with `use: call` or `use: node`, Plan 12.3), so it can build a plan without the Composer. `delegates: [composer, recovery_agent]` (its SDK subagents); `skills: [research_study, modeling]`; `model_profile: main`.
2. **`composer`** (`kind: internal`, `parent: main_agent`):
   - tools: the read-only `main` tools (`graph_inspect`, `registry_search`, `registry_describe`, `artifact_read`) and `graph_propose`;
   - its prompt says it **never** applies or executes anything: it proposes with `graph_propose`, reads the Validator's errors in the result, proposes again until there are none, and ends with the `proposal_id` and a short explanation. The repair loop is the SDK's own agent loop; there is no repair code of ours. `skills: [research_study, modeling]`; `model_profile: composer`.
3. **`recovery_agent`** (`kind: internal`, `parent: main_agent`):
   - the same tools as the Composer;
   - its prompt says it diagnoses the failed node it is given, may propose a fix with `graph_propose` (a `RerunNode`, new params, a `seed_from`), and ends with the diagnosis, the optional `proposal_id` and a message for the user. It never applies anything. `model_profile: recovery`.

**Notes:**

- `catalog.validate()` accepts `delegates` on `main_agent` only among control agents; the Composer and the Recovery Agent are internal agents of the Main (subagent depth 1).
- The Main chooses its tools by visibility and never lists tool names, so `validate()` passes before the control tools exist (Plan 12).
- This phase creates `planning/graph_model.py` with only the `GraphDiff` and `GraphProposal` models (the input of `graph_propose`). Plan 8 fills in the rest of the graph model.

## Phase 3.5 — Skills (the SDK's skills, from `agent_system/skills/cubeai/`)

1. **The format: the SDK's own skills** (decided 2026-10-20). `agent_system/skills/cubeai/` is a local SDK plugin (`.claude-plugin/plugin.json` with `name: cubeai`) that holds **only** `skills/<name>/SKILL.md` files: no `agents/`, `hooks/`, `commands/` or `.mcp.json`. A `SKILL.md` has front matter with `name` and `description` (the description tells the model when to load it), and its body is guidance text.
   - **Who gets which skill:** an `agent.md` lists its skills by name (`skills: [modeling]`; the default is none). The engine passes the list to the SDK (Phase 9.4), so a session sees only its own skills and never the CLI's bundled ones.
   - **How a skill is used:** the SDK shows the listed skills' descriptions to the model, and the model loads a body with the `Skill` tool when it needs it. Nothing of ours builds an index, and there is no `skill_load` tool.
   - **Forbidden in a `SKILL.md`:** `allowed-tools` and other permission fields in the front matter, and `` !`command` `` lines (they run a shell command before the model sees the skill). A skill is text only.
2. **Initial skills:**

   | Skill | Content copied from | Used by |
   |---|---|---|
   | `research_study` | The HypothesisArchitect and PipelineArchitect prompts in `planning_agents.py` (PICO; the typical radiomics flow) | `main_agent`, `composer` |
   | `modeling` | The `modeling_agent.py:36-99` prompt: choosing the target, task type, comparisons and folds, rewritten as advice on `run_modeling` params and the engine's estimator ids (Plan 7). No PyCaret names or PyCaret functions. | `main_agent`, `composer` |
   | `cohort_building` | The population agent prompt (criteria, labels, cohort size) | `study_population_planner_agent` |
   | `segmentation_choices` | The segmentation prompt (model families, review) | `segmentation_agent` |
   | `reporting` | The result and LaTeX reporter prompts | `result_reporter_agent`, `latex_reporter_agent` |

3. **Rule:** a skill is guidance text. It never routes and never forces a workflow.
4. **Internal agents** list no skills, except the Main's two subagents (the SDK's `AgentDefinition.skills`).

**Tests (`tests/unit/agentpacks/`):**

- loader errors (bad front matter, unknown port type, missing prompt);
- each spec's allowlist, iterations and ports; the conditional tool; the `completion_check` wiring;
- the SDK fields: `resumable` defaults per kind; `effort` values; an internal agent with `delegates`, and an explicit `ask_user` in `allowed_tools`, are refused; the old fields `gates`, `workflow` and `human_in_the_loop` are refused with a clear error;
- the skills: every name in an agent's `skills` has a `SKILL.md` in the plugin; the plugin holds nothing but `skills/`; no `SKILL.md` has `allowed-tools` or a `` !`command` `` line; `catalog.validate()` passes with all agents.

**Done when:**

- the catalog has **16 agents** (1 control, 6 execution, 9 internal: the Main's 2, segmentation's 2, literature's 5) and `validate()` passes; at the first working flow it has **10** (`main_agent`, `composer`, the 6 execution agents, the 2 segmentation internal agents) and `validate()` passes;
- every execution agent declares its ports.

---

# Plan 4 — removed (no sandbox; a job runs in its own child process)

---

# Plan 5 — Job Platform (PostgreSQL + Procrastinate)

**Owner:** Person 1 (Compute jobs). See [team-plan.md](team-plan.md).

**Goal:** heavy tools run as jobs queued in the Application API's PostgreSQL with Procrastinate, behind one `JobClient` contract, each in its own child process. Jobs have deterministic ids, real cancel, progress, timeouts, retention, restart recovery, OOM detection and queue liveness, with no Redis, no Celery and no beat process.

**Decisions (2026-10-09):**

- **One database.** The queue is Procrastinate's tables in the Application API's PostgreSQL. There is no Redis, no Celery, no `compute-beat` and no Flower.
- **Only the Application API sends and cancels jobs.** The Agent System reaches jobs only through `JobClient` → Application API. It never touches the queue or the database.
- **One owner per state (D12).** Procrastinate's rows hold the physical queue state (`todo`, `doing`, `succeeded`, `failed`, `cancelled`, `aborted`) and the worker heartbeats. `run_attempts` holds the attempt lifecycle and outcome, written only by T4–T7 from what was observed. Nothing copies one into the other: the Application API reads the queue through one view, `job_execution_status`, so a Procrastinate upgrade changes one migration.
- **The job dir is the job's record.** `COMPUTE_JOBS_DIR/{submission_key}/` holds `request.json`, `progress.json`, `process.json` and `result.json`. `result.json` is always written, atomically, even on error; a large result stays a file ref in the dir (claim check). **`result.json` wins:** when it exists, it is the job's outcome, whatever the queue row says. The Node Runner reads it through `JobClient.result` and completes the attempt itself (T6), so the worker reports nothing after the work but `result.json`.
- **No Procrastinate retries** (`retry=False`). A retry is a new attempt opened by T4 (I8, D9). A stalled job is never re-run; it is observed as `INTERRUPTED`, and the Watchdog decides (Phase 10.5).
- **Queues are the `resource_class` names:** `compute.cpu` (started), `compute.gpu` and `compute.highmem` (declared only). One worker process per queue per machine; its `--concurrency` is that queue's limit on that machine.
- **MONAI is not a Procrastinate queue.** Segmentation inference (Plan 15) and training (Phase 16.2) stay on MONAI's own job store, which owns the GPU it runs on.
- **Version-specific behaviour is tested, not assumed:** the abort of a running async task, stalled-job detection, worker heartbeats and `queueing_lock` after a job starts are checked against the pinned Procrastinate version by the integration suite.

## Phase 5.1 — Job contract and client library (`Backend/jobs_core/`)

1. **`contract.py`:**
   ```
   JobRequest {
     job_id:  sha256(run_id | job_name | canonical_json(args))   # run_id = the node run (Plan 6) -> resume re-attaches
     submission_key: f"{job_id}.{attempt_number}"              # = run_attempts.submission_key (Appendix P)
     attempt, job_name (a REGISTERED tool name, never an import path), job_version,
     args (JSON only), context (ToolContext.to_payload()),
     resource_class (derived from resources),
     timeout_s, queue_timeout_s, priority
   }
   JobRecord { id, status, error_code, attempt, input_hash, progress, queued_at,
               started_at, finished_at, error, result | result_ref, host }   # built from the view row + the job dir
   ```
   `error_code` is one of: `error`, `timeout`, `queue_timeout`, `oom`, `interrupted`, `cancelled`, `run_not_running`.
2. **`status.py`:** `QUEUED RUNNING CANCELLING SUCCEEDED FAILED TIMED_OUT CANCELLED INTERRUPTED`, plus `TERMINAL`, `ACTIVE` and `is_final()` (an unknown status counts as final).
3. **`derive_status(row, job_dir)`**, the only mapping from queue state to job status:
   - `result.json` present → its status;
   - `todo` → `QUEUED`; `doing` with a live worker → `RUNNING` (`CANCELLING` once a cancel is requested);
   - `doing` with a worker heartbeat older than `JOB_HEARTBEAT_TIMEOUT_S` → `INTERRUPTED`;
   - `cancelled` before it started → `CANCELLED`, or `TIMED_OUT` (`queue_timeout`) when the queue sweep cancelled it;
   - `failed` with no `result.json` → `FAILED` (`error`): the supervisor itself crashed;
   - no row → not found.
4. **`client.py`:** the `JobClient` protocol: `submit` (the same key returns the existing record), `get`, `wait(budgets, on_progress, cancel)`, `result`, `cancel`, `purge`, `list(run_id, active_only)`.
5. **`polling.py`** (moved from `toolpacks/common/polling.py`, see the Plan 2 follow-ups): `RetryPolicy` (backoff, jitter, cap, `Retry-After`) and `poll_until()` with a monotonic, pause-aware deadline.
6. **`budgets.py`:** `JobBudgets(queue_s, run_s, result_s, unavailable_s)`, where the outer budget = the sum of the inner budgets + a margin.
7. **`backends/in_memory.py`** for tests. The HTTP backend, `backends/app_api.py`, is Phase 5.4.

## Phase 5.2 — Application API: the queue, dispatch and cancel

1. **Dependencies:** `procrastinate` (pinned) and `psycopg[binary,pool]`. A Procrastinate `App` with a `PsycopgConnector` whose DSN is derived from `DATABASE_URL` (without `+asyncpg`), opened and closed in the lifespan.
2. **Migration "s"** (`s1f0a2c3d4e5_v1_s_job_queue`, after `r1f0a2c3d4e5`):
   - the Procrastinate schema of the pinned version, applied from its own SQL file, unchanged;
   - the view `job_execution_status`: `submission_key` (the job's `queueing_lock`), queue, status, queued / started / finished times, worker id, the worker's last heartbeat;
   - grants on the Procrastinate objects, and nothing else, to the role `cubeai_jobs` (created by the PostgreSQL init script from `JOBS_DB_PASSWORD`).

   A Procrastinate upgrade is a new migration with its upgrade SQL and the view.
3. **T4 dispatch for jobs:** `POST /runs/{run_id}/attempts/{attempt_id}/dispatch` (attempt path).
   - Require the attempt current, `dispatch_kind='job'`, and `preparing` (or `submitted` / `running` for a resend, which changes nothing).
   - Defer `compute.run_tool_job` with the attempt's stored `request`, `queue = request.resource_class`, `queueing_lock = lock = submission_key`, `retry=False`; then commit `submitted` + `dispatched_at` ("send, then commit", Appendix P T4). The defer runs on Procrastinate's own connection before the T4 dispatch transaction opens, so no transaction spans it.
   - `AlreadyEnqueued` for the same lock, or a row already present for the key, counts as sent.
   - `/dispatched` stays for `in_process` and `inline` attempts.
4. **T7 for jobs:** `request_cancel` on a job attempt also cancels its Procrastinate job in the same request: a `todo` job is cancelled and never starts; a `doing` job gets an abort. The worker also polls `cancel_requested_at` (Phase 5.3).
5. **Reads:** `GET /runs/{run_id}/attempts/{attempt_id}/job` (the view row for the attempt's key, `owned_run`), and `GET /jobs/queues` (per queue: `todo`, `doing`, live workers) for Plans 11 and 13.
6. **T5 and T6 do not change:** the Node Runner observes the job through `JobClient.get` and reports the outcome (T5) or completes the attempt (T6), as for any backend.

## Phase 5.3 — Compute worker (`Backend/compute_worker/`, a Procrastinate app)

1. **`app.py`:** `App(connector=PsycopgConnector(conninfo=JOBS_DATABASE_URL))`; JSON arguments only; one task, `compute.run_tool_job(request)`, with `retry=False`.
2. **`launcher.py`** (the child launcher; there is no sandbox):
   - `asyncio.create_subprocess_exec(..., start_new_session=True)`, so a stop reaches the child's own subprocesses;
   - an **env allowlist** (no `MODEL_API_KEY`, no `JOBS_DATABASE_URL`, no tokens);
   - stdout and stderr go to the job dir;
   - `stop(grace_s)`: `killpg(SIGTERM)` → wait `grace_s` → `killpg(SIGKILL)` → reap.
3. **`supervisor.py`**, in order:
   1. **Already done?** A complete `result.json` → return. The work finished before a crash, so nothing is computed again.
   2. **Queue timeout:** `queued_at + queue_timeout_s` has passed → write a `TIMED_OUT` (`queue_timeout`) result and return.
   3. **Start:** move the attempt to `running` through the Application API (`POST /runs/{run_id}/attempts/{attempt_id}/running`). A refusal (the attempt is no longer the run's active one; H20) writes `run_not_running` and starts no child.
   4. **Launch:** start the child through the launcher; `{host, pid, pgid}` go to `process.json`.
   5. **Watch,** every `JOB_POLL_S`: the attempt's `cancel_requested_at`, the Procrastinate abort, and `timeout_s`. A stop is `launcher.stop(JOB_KILL_GRACE_S)`.
   6. **Exit:** write `result.json` (`SUCCEEDED`, `FAILED`, `TIMED_OUT` or `CANCELLED`) through a temp file and a rename, then **return normally**, so a queue `failed` means only that the supervisor itself crashed. **OOM:** a child that reports `MemoryError`, or that is killed by SIGKILL (exit code 137) without a stop request, is recorded as `FAILED` with `error_code="oom"`.
   7. **Cancelled coroutine** (an abort, or the worker shutting down): stop the process group in `except CancelledError` and in `finally`, then write `CANCELLED` (abort) or `INTERRUPTED` (shutdown).
4. **`child.py`:**
   - calls `ensure_storage_layout()` and `install_write_guard()`, and sets up a per-job log handler;
   - applies thread caps (`OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`, `COMPUTE_CPU_THREADS`);
   - builds `ToolContext.from_payload()` with a `FileProgressSink` (`progress.json`). The payload carries `run_id` and `attempt_id`, so every write the handler makes presents the D24 attempt path;
   - looks up `job_name` in the catalog (an **allowlist**: only `execution="job"` tools);
   - runs the **same tool handler**, and always writes a result, even on error.
5. **Periodic tasks** (Procrastinate periodic tasks on the `compute.cpu` worker):
   - a `doing` job whose worker heartbeat is older than `JOB_HEARTBEAT_TIMEOUT_S` is marked failed, and reads as `INTERRUPTED` through `derive_status`;
   - a `todo` job past its `queue_timeout_s` gets its `queue_timeout` result, then is cancelled;
   - finished jobs older than `JOB_RETENTION_HOURS` are deleted, and so are job dirs left behind that long.
6. **Start-up:** kill the process groups recorded in `process.json` on this host whose job no live worker holds, and write `INTERRUPTED` for them.
7. **GPU queue** (declared; started when the first GPU job tool exists): one `compute.gpu` worker per machine, `--concurrency` = the number of GPUs, and an in-worker pool of free GPU indices. A slot takes an index, sets `CUDA_VISIBLE_DEVICES` for its child, and returns it in `finally`. `CUDA_VISIBLE_DEVICES` alone is not a reservation.
8. **`Backend/storage_paths.py`:** add `COMPUTE_JOBS_DIR` (and document it in `Backend/storage/README.md`). Every output a tool keeps goes through `storage_paths` / `stored_files`, never `/tmp`, so a second machine needs only a shared mount.

## Phase 5.4 — Toolkit integration

1. **`toolkit/runtime/job.py`:**
   - builds the `JobRequest` from the spec and the context;
   - runs `JobClient.submit` → `wait` (progress goes to the `EventSink` as `tool_progress`) → `result`, which returns the same JSON as today;
   - maps the cancel token to `JobClient.cancel`, and calls `purge` in `finally`;
   - `dispatch.py` routes `execution="job"` here.
2. **`jobs_core/backends/app_api.py` (`AppApiJobClient`):**
   - `submit` → `…/dispatch`; `get` → `…/job` plus the job dir, through `derive_status`;
   - `wait` → `poll_until` with the budgets, relaying `progress.json` on each poll (H19);
   - `cancel` → T7 (`…/cancel`); `result` → `result.json`; `purge` → deletes the job dir after T6; `list` → the run's job attempts.

## Phase 5.5 — Deploy and CI

1. **Requirements:**
   - the agents image (the worker) and the application-api image: `procrastinate` (pinned) and `psycopg[binary,pool]`;
   - no `celery`, `redis` or `fakeredis`;
   - remove `fastmcp`; `mcp` stays only as a dependency of the Claude Agent SDK (Plan 9);
   - `Backend/agent_system/requirements.txt` is **UTF-16 LE with CRLF**, so keep that encoding.
2. **`deploy/docker-compose.backend.gpu.yml`:**
   - `compute-worker-cpu`: the agents image, running `procrastinate --app=compute_worker.app.app worker --queues=compute.cpu --concurrency=${COMPUTE_MAX_CONCURRENCY:-1}`, with `JOBS_DATABASE_URL` and `APP_API_URL`. It uses the **same `/storage` mount path**, a non-root user, no Docker socket, and a `mem_limit` (`COMPUTE_MEMORY_LIMIT`), so an out-of-memory job is killed inside the container. Its network reaches only PostgreSQL and the Application API. `init: true`, and a `stop_grace_period` longer than `JOB_KILL_GRACE_S`;
   - `compute-worker-gpu`: declared under the compose profile `gpu-jobs`, not started;
   - the PostgreSQL init script creates the role `cubeai_jobs`;
   - remove the `Backend/mcp` bind mount (`:13`).
3. **`deploy/Dockerfile.agents`:** copy `jobs_core` and `compute_worker`; remove `COPY ./Backend/mcp` (`:40`).
4. **`deploy/.env.example`:** `JOBS_DB_PASSWORD`, `JOBS_DATABASE_URL`, `COMPUTE_MAX_CONCURRENCY`, `COMPUTE_CPU_THREADS`, `JOB_RETENTION_HOURS`, `JOB_HEARTBEAT_TIMEOUT_S`, `JOB_KILL_GRACE_S`, `JOB_POLL_S`, `COMPUTE_MEMORY_LIMIT`.
5. **The database boundary:** `DATABASE_URL` only on `application-api`, and `JOBS_DATABASE_URL` only on `compute-worker-*`. The test that checks the compose files, and `CLAUDE.md`, say so.
6. **pytest:** the `celery` marker (Phase 1.5) becomes `jobs` ("needs PostgreSQL and a compute worker"), and the `sandbox` marker is removed.
7. **CI:** the jobs integration suite runs in `live-db-tests` against its `postgres:18-alpine` service, with a worker started from the built image. No Redis service.

**Tests:**

- **`tests/unit/jobs/`** (in-memory, plus Procrastinate's `InMemoryConnector`):
  - job id and input hash; dedupe on `queueing_lock`; a conflict for the same id with different args;
  - one submission key per attempt; resubmitting the same key returns the record;
  - the `derive_status` table, row by row (`result.json` wins; a stale heartbeat → `INTERRUPTED`; the queue sweep → `TIMED_OUT(queue_timeout)`; a supervisor crash → `FAILED`);
  - retry policy / `Retry-After`; the pause-aware deadline;
  - the supervisor with a fake runner: the `result.json` short-circuit, the OOM mapping, the SIGTERM → grace → SIGKILL order, `run_not_running`;
  - the launcher: no secret and no `JOBS_DATABASE_URL` reach the child; a stop reaches grandchildren; the SIGTERM → grace → SIGKILL order;
  - the GPU pool: two GPUs and two jobs get different indices, and an index comes back after a failure.
- **`tests/integration/jobs/`** (`-m "integration and jobs"`, with real PostgreSQL and a real worker, **on the pinned Procrastinate version**):
  - submit → progress → result;
  - cancel a queued job: it never starts;
  - cancel a running job whose handler starts a child and a grandchild: both are gone; a child that ignores SIGTERM is killed after the grace period;
  - the Procrastinate abort of a running async task reaches the supervisor's `finally`;
  - soft and hard timeouts; a cancel that arrives after the job finished changes nothing;
  - the worker killed mid-job → `INTERRUPTED` after the heartbeat timeout; the worker container stopped mid-job → the job stops within the grace period;
  - the worker killed after `result.json` was written → the outcome is read back, and nothing is computed again;
  - `run_not_running`; a write with a stale `attempt_id` is rejected; the write guard is active in the child.
- **Contract:** `job_execution_status` exists with the expected columns; `cubeai_jobs` has rights on the Procrastinate objects only; nothing outside `compute_worker/` and the Application API imports `procrastinate`; no `redis` or `celery` import or requirement anywhere.

**Done when:** the unit and integration job suites pass; `runtime/job.py` works with both the in-memory and the `app_api` backends; and no Redis or Celery is left in the requirements, the compose files or the code.

---

# Plan 6 — Session State Store: Persistence Design (Application API) (6.1–6.5 ✅; 6.6 open)

**Owner:** Phase 6.6: Person 3 (Execution and session state). Phases 6.1–6.5 are done. See [team-plan.md](team-plan.md).

**Goal:** implement Appendix P (the persistence design, revision 3.4; 6.1–6.5 built revision 3.2, and Phase 6.6 brings it to 3.4): the database, the transactions T0–T10 and the HTTP surface the harness uses. The Application API owns every row, and the Agent System reaches it only over HTTP. Appendix P is the specification; this plan says in which order it is built and how it is tested, and never restates its column lists.

## Phases 6.1–6.5 — what exists (read the code in `Backend/application_api/`)

Done (2026-10-06): migrations `o1f0a2c3d4e5_v1_o_renames` (the renames of §P.2), `p1f0a2c3d4e5_v1_p_session_graph` (the session-graph schema) and `q1f0a2c3d4e5`. What later plans build on:

- **Pure rules:** `core/graph_content.py` (`ChangeContent`, `plan_apply()`: inserts, edits, removals, the affected suffix, supersession, `PLAN_MODE` / `supersedes_completed`) and `core/execution_rules.py` (outputs allow-list, expectations, retry rules (b)–(f), derived activity).
- **D24 fencing** (`services/fencing.py`): a write route on the execution path takes `Depends(fenced_db)` (from `X-Attempt-Id` / `X-Run-Id`); a harness or gate transaction opens `fenced_transaction(db, HARNESS | USER)`; `@on_path(USER)` wraps the in-node gate repositories. A database refusal is a 409 `{code: REFUSED_BY_DATABASE, …}`; harness refusals carry stable codes (`STALE_BASE`, `IDEMPOTENCY_CONFLICT`, `NOT_READY`, `ATTEMPT_ACTIVE`, `STALE_DEFINITION`, `BAD_PIN`, `MAX_ATTEMPTS`, `NEEDS_DECISION`, `CANCELLED`, `NOT_RESUMABLE`, `NO_OPEN_GATE`, `INVALID_CHANGE`). `tests/contracts/test_v1_harness_contract.py` scans the write routes for it.
- **Repositories** for T0–T10: `sessions`, `chats`, `graph_changes` (T1/T2), `node_runs` (T4), `run_attempts` (T4–T7, T10), `training_jobs` (T9). Every lifecycle transaction publishes `node_status` (schema v2) and `session_state`.
- **Content shapes** (also the `graph_nodes` columns): `input_sources = {v, ports: {port: {node_id, port}}}`, `expect = {v, rules: [{metric, op, value, port?}]}`. The `seed_from` built here names a LangGraph checkpoint; Phase 6.6 and the Plan 8 follow-up change it to `{run_id, message_uuid}`.
- **Routes:** nodes change only through `POST /session-graphs/session/{id}/changes`; runs start only through `POST /runs/start`; the old plan approve route answers `410 Gone`. Every session-scoped route checks ownership through an `owned_*` dependency (`api/dependencies.py`; another user's session is a 404), enforced by `tests/contracts/test_route_ownership_contract.py`. The user is `DEFAULT_USERNAME` until authentication exists.
- **Agent System client:** `clients/harness_client.py` (`HarnessClient`: one method per route, refusals as `ApplicationApiError(code)`; an attempt's own writes send `X-Attempt-Id` from the attempt id they are given). `execution/graph_store.py` (`GraphStore`, `HttpGraphStore`) and `execution/in_memory_graph_store.py` (`InMemoryGraphStore`) pass one scenario table (`tests/support/graph_store_scenarios.py`).
- **Old names kept on purpose:** tool result keys such as `agent_run_id` and the `output_summary` argument, each marked `tool result key, kept`; `tests/contracts/test_retired_vocabulary.py` accepts them.
- **Tests:** the deterministic gate, and the live database suites `Backend/tests/run_live_db_tests.ps1` (throwaway PostgreSQL 18, never the compose one).

**Replaced by Phase 6.6:** the LangGraph checkpoint tables and routes built here (`agent_checkpoints`, `agent_checkpoint_writes`, `/runs/{run_id}/checkpoints*`, `/agent-checkpoints/purge`) and the checkpoint reads in T4. The Agent System's `/input` still queues in memory until the Main loop moves it onto T3 (Plan 12).

## Phase 6.6 — Cut-over 3: agent transcripts and user-requested approvals (migration `t1f0a2c3d4e5_v1_t_agent_transcripts`, after the head at the time: `r1f0a2c3d4e5`, or `s1f0a2c3d4e5` once Plan 5 lands)

Implements Appendix P revision 3.4 (D27 rewritten, D28 new; §P.9 *Cut-over 3*). Lands before Plan 9.5, which writes through these routes.

1. **The migration:**
   - drops `agent_checkpoints` and `agent_checkpoint_writes` (they hold no production data: nothing has written them outside tests) and creates `agent_transcript_entries` with the shared fencing trigger;
   - creates `main_transcript_entries` (the Main's saved SDK session, D21): key `(session_id, subpath, seq)`, unique `(session_id, subpath, entry_uuid)`, rows immutable; not an execution table, so no fencing trigger;
   - `run_attempts`: adds `pending_tools`; the `suspension` contract becomes `v:2` (`kind: ask_user|tool_approval|pause`, `tool_use_id`, no `checkpoint_id` or `interrupt_id`);
   - `graph_nodes`: adds `approval` (`none|before|after`, default `none`) and `start_released_by_change_id`;
   - `node_runs`: the `downstream_hold` CHECK accepts `user_review`;
   - the automatic-approval values: `cohorts.frozen_by` (`user|auto`), `segmentation_batches.review_status` gains `auto_accepted`, `segmentation_pairs.review_decision` gains `auto`;
   - `chat_messages`: drops the Main's tool-call contract (the unique index on `(chat_id, turn_id, call_id)` for `tool_result` rows and the trigger that pairs a `tool_result` with its `tool_calls` row); the Main's tool calls are in its SDK transcript (D21). Existing rows stay as history;
   - `downgrade()` refuses when any transcript entry, `approval` other than `none`, `user_review` hold or automatic-approval value exists.
2. **Repositories and transactions:**
   - `append_transcript` / `load_transcript` / `list_transcript_subpaths` (attempt path for writes: the run's latest attempt, `running` or `suspended`; reads by any attempt of the run, and by the first attempt of a run whose frozen `seed_from` names it);
   - `purge_transcripts` (harness path; runs terminal for more than `AGENT_TRANSCRIPT_RETENTION_DAYS`, default 14, that no live `seed_from` names; and the Main transcripts of sessions archived longer than the same retention);
   - `append_main_transcript` / `load_main_transcript` / `list_main_transcript_subpaths` (owner-checked; one writer by the single-worker rule);
   - `set_pending_tools` (attempt path; the attempt's own column);
   - **T2:** an `approval` change is a definition edit; editing or rerunning a node clears `start_released_by_change_id`; a `ReleaseHold` on an `approval: before` node that has not started sets `start_released_by_change_id`; a `ReleaseHold` that releases a `user_review` hold or a start hold is refused unless the change's author is `user`;
   - **T4:** refuses the start of an `approval: before` node until `start_released_by_change_id` is set (`NEEDS_RELEASE`); retry rule (f) reads the latest attempt's `pending_tools`; a run whose latest attempt failed with `transcript_write_failed` is never retried automatically; a seeded first start only checks the seed (it exists, belongs to a run of the same node, the `privacy_mode`, `spec_digest` and `engine_version` match) and freezes `seed_from.applied`: the copy is the SDK's fork (Plan 9.4);
   - **T6:** an `approval: after` node's completed current run gets `downstream_hold = 'user_review'` and publishes `approval_requested`;
   - **T1 / T2:** they stop writing the Main's `tool_result` row into the chat (the Main's tool results are in its SDK transcript, D21 revised 2026-10-20); the idempotency key of a Main change is `sha256(session_id‖tool_use_id)`: `main_idempotency_key(session_id, tool_use_id)` in `repositories/graph_changes.py`, and the change schema's `tool_call {turn_id, call_id}` becomes `{session_id, tool_use_id}`;
   - **Removed code:** the `agent_checkpoints` models, repositories, schemas and routes;
   - **T10:** suspend and resume as before, with the `v:2` suspension; the answer step (user path) records `suspension.resume = {answer | decision, comment, decided_at}` and, for a domain form, the domain columns in the same transaction.
3. **Routes:**

   | Method | Path | What |
   |---|---|---|
   | POST | `/runs/{run_id}/transcript` | Append entries (attempt path); body `{subpath?, project_key, entries}`; a replayed `entry.uuid` is a no-op |
   | GET | `/runs/{run_id}/transcript` | The entries in order (`?subpath=`), or 404 |
   | GET | `/runs/{run_id}/transcript/subpaths` | The run's subagent subpaths |
   | PUT | `/runs/{run_id}/attempts/{attempt_id}/pending-tools` | Set the attempt's pending calls (attempt path) |
   | POST | `/runs/{run_id}/attempts/{attempt_id}/answer` | T10 answer for an `ask_user` question or a tool approval; body `{tool_use_id, answer? , decision?, comment}`; user only. Replaces `/gate-decision` |
   | POST | `/agent-transcripts/purge` | Retention purge (harness path) |
   | POST, GET | `/sessions/{session_id}/main-transcript` (`?subpath=`), `/sessions/{session_id}/main-transcript/subpaths` | The Main's saved session (append, load, subpaths); a replayed `entry.uuid` is a no-op |

   Removed: `/runs/{run_id}/checkpoints*`, `/runs/{run_id}/checkpoint-writes`, `/runs/{run_id}/steps` (the step timeline moves to the Agent System, which reads it with the SDK's helpers, Plan 12.7), `/agent-checkpoints/purge`, `/gate-decision`. The domain form routes (`/populations/approve-cohort`, the segmentation review) also run the T10 answer step when an attempt is suspended on that form.
4. **Agent System client:** `HarnessClient` and `GraphStore` get the transcript, pending-tools and answer methods and lose the checkpoint ones; `InMemoryGraphStore` keeps the same invariants (transcript fencing, `NEEDS_RELEASE`, user-only release).

**Tests:**
- **Contract:** the fencing trigger is attached to `agent_transcript_entries`; the retired checkpoint tables and routes are gone; every new column has its CHECK.
- **Integration** (live database): a transcript write from a stale or terminal attempt is refused; attempt 2 reads attempt 1's entries; a replayed entry is a no-op; a seeded run may read its seed's transcript and no other; `NEEDS_RELEASE` until a user `ReleaseHold`; a Main `ReleaseHold` of a `user_review` hold is refused; `approval: after` sets `user_review` on completion; rule (f) with a non-idempotent pending tool blocks the automatic retry; `transcript_write_failed` is never retried automatically; the purge keeps entries a live `seed_from` names.
- **Scenarios:** the `graph_store_scenarios.py` table gains the approval and transcript cases and runs on both stores.

**Done when:** migration t applies and reverses, and every test above passes.

---

# Plan 7 — Modeling Engine, Heavy Tools as Jobs + `run_modeling`

**Owner:** Person 1 (Compute jobs). Phases 7.0–7.2 are done. See [team-plan.md](team-plan.md).

**Goal:** PyCaret is replaced by a scikit-learn modeling engine. The 7 heavy tools and the new `run_modeling` tool run as jobs on the job platform (Plan 5). They are safe to run twice and at the same time, report progress, and can be used as graph nodes.

## Phases 7.0–7.2 — what exists (read the code in `toolpacks/modeling/`)

Done (2026-10-06):

- **Engine** (`modeling/engine/`): `estimators` (registries keyed by the short model ids `lr`, `rf`, `xgboost`, …, which old records and prompts already use; no CatBoost), `preprocessing` (a scikit-learn `Pipeline`), `cv`, `metrics`, `compare`, `tune` (a seeded `ParameterSampler`; it keeps the untuned model when nothing beats it), `blend`, `plots` (the Agg canvas, no pyplot), `persistence` (a joblib dict `{engine, format, task, pipeline, features, classes}`, written atomically), `fitting`, `dataset`. `fix_imbalance` is balanced sample weights (no SMOTE). `n_jobs` comes from `COMPUTE_CPU_THREADS`.
- **Tools:** `train_classifier`, `train_regressor`, `run_classifier_inference`, `run_regression_inference`, `refit_model` (renamed from `pycaret_*`, same params and success shapes; shared bodies in `modeling/training.py` and `modeling/inference.py`). They are still `INTERIM_HEAVY` with `visibility={"agent"}`. New records write `parameters.model_id`, `preprocessing.pipeline` and `environment.engine = "sklearn"`.
- **Legacy PyCaret records** stay readable. Inference on one answers `legacy_model_format` and names `refit_model`, which rebuilds it on the engine as a new variant (`environment.refit_of`).
- The frozen `services/pipeline_monitor.py:227` still matches `pycaret_train_classifier`; the `on_result` hooks of Phase 7.5 replace it.

## Phase 7.1 — Manifests and ports of the heavy tools

**Base manifest for all 7 heavy tools** (the job fields come with the job platform; until then they stay `INTERIM_HEAVY`, inline, with the ports and visibility below):
- `execution="job"`, `side_effects="compute"`, `cost="heavy"`, `resources=["cpu_heavy"]`;
- `trust="first_party"`, `isolation="process"`.

| Tool | `visibility` | Ports |
|---|---|---|
| `train_classifier`, `train_regressor` | {`graph`} | `features: FeatureTable` → `modeling: ModelingOutput` |
| `run_classifier_inference`, `run_regression_inference`, `refit_model` | {`graph`} | `modeling: ModelingOutput` (+ `features`) → `modeling: ModelingOutput` |
| `radiomics_extract_features` | {`agent`, `graph`} | `cohort: Cohort`, `masks: MaskSet` → `features: FeatureTable` |
| `radiomics_analyze_importance` | {`agent`, `graph`} | `features: FeatureTable` → `importance: FeatureImportance` |

The worker runs the same handlers, so no wrapper code is needed.

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
- H13: the run's temp cleanup (now done by the Node Runner at completion, Phase 10.1, since `write_agent_output` is gone) is skipped while `JobClient.list(run_id, active_only=True)` is not empty.

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
- (`-m "integration and jobs"`) each tool, and `run_modeling`, runs in `child.py` on a tiny synthetic CSV or a tiny synthetic NIfTI.

**Done when:** all 8 tools run through the job runtime on the scikit-learn engine, nothing imports `pycaret`, and the heavy and engine tests pass.

---

# Plan 8 — Validator and Diff Engine

**Owner:** The follow-up: Person 4 (Agent engine and models). Phases 8.1–8.4 are done. See [team-plan.md](team-plan.md).

**Goal:** deterministic graph code for a sequential live graph: a graph model, a Validator that returns only technical errors, the strict input resolution (D23), the affected set, and atomic, versioned changes through T1/T2 (system §9, §26, §27; Appendix P).

## Phase 8.1 — Graph model (`planning/graph_model.py`, pure Pydantic)

1. **The model:**
   - `NodeDef(node_id, kind, ref, ref_version, title, params, input_sources, expect, approval)`. `node_id` is a UUID minted by whoever proposes the node (D7); there are no local refs. `title` is part of the definition (`graph_nodes.title`), so it is in the change content and renaming a node is an edit. `approval` is `none | before | after` (D28, added 2026-10-19), also part of the definition;
   - `InputSource(node_id, port)`; `Expectation(metric, op, value)`, where `op` is one of `== != < <= > >=`;
   - `GraphState(version, nodes)`: the live sequence in `position` order, each node with a summary of its current run.
2. **`GraphDiff(base_version, ops, on_running = "reject" | "restart")`.** The ops are:
   - `AddNode(at?)`, `RemoveNode`, `MoveNode`, `UpdateParams`;
   - `SetInputSources`, `SetExpectations`, `ReleaseHold`, `RerunNode(seed_from?)`.

   There is no `Rebind` or `AddAfter`: the order is the sequence (D5).

   `seed_from = {run_id, message_uuid}` (agent nodes only, D27; `checkpoint_id` until the 2026-10-19 follow-up) asks the new run to start from that message of an earlier run's transcript of the **same** node instead of from scratch, for example the step before a bad tool call. It is still a new run (I8), so it supersedes like any rerun and carries `supersedes_completed` when it should.

   `ReleaseHold` releases an expectation hold, a `user_review` hold, or the start hold of an `approval: before` node that has not started (D28). The last two only when the change's author is the user.
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
| `BAD_SEED` | `seed_from` on a tool node or a non-`resumable` agent, or its `run_id` is not a run of this node. Whether the seed's transcript still exists and is compatible is checked by T4, which then starts fresh |
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

2. **`apply(diff, author, turn_id?, call_id?)`:** runs the Validator, then `GraphStore.submit_change`: T2 when there are no approval reasons, T1 otherwise. For the Main the idempotency key is `sha256(turn_id‖call_id)` (the follow-up below changes it to `sha256(session_id‖tool_use_id)`). On success it wakes the Executor (`graph_changed`). The durable events come from the Application API.
3. **`decide(change_id, decision, content_digest)`** for user decisions (Plan 11).

**Tests (`tests/unit/planning/`, `tests/unit/execution/test_diff_engine.py`):**
- every Validator code, including `AMBIGUOUS_INPUT` for two compatible producers and for one node with two compatible ports, and an insert that makes a later node ambiguous;
- the §27 matrix (3 states × affected/unaffected × `on_running`);
- the A→New→B example (B is affected); `affected()` equals the T2 rule;
- `RerunNode(seed_from)`: accepted for a run of the same resumable agent node, `BAD_SEED` otherwise; it supersedes like any rerun;
- `ReleaseHold` of a `user_review` hold or a start hold by a non-user author is refused (`USER_RELEASE_REQUIRED`, follow-up 2026-10-19);
- a stale base; new node ids unused; superseded outputs are never deleted.

**Done when:** all the Validator and Diff Engine tests pass with `InMemoryGraphStore`.

## Plan 8 — follow-up (decision 2026-10-19)

Phases 8.1–8.4 are built (`ce5a777e`, `3ac5411c`). The decision of 2026-10-19 changes three things in that code, together with Phase 6.6:

1. **`seed_from`:** `SeedFrom.checkpoint_id` becomes `message_uuid` in `planning/graph_model.py`, `application_api/core/graph_content.py` and the `BAD_SEED` check of `planning/validator.py`; the "agent nodes only, `resumable` only, a run of the same node" rules stay.
2. **`approval`:** `NodeDef.approval` (`none | before | after`, default `none`) in the graph model and in `ChangeContent`; changing it is an edit, so it is in the affected set like any definition change.
3. **`ReleaseHold`:** `apply(diff, author, …)` refuses a `ReleaseHold` that would release a `user_review` hold or a start hold unless `author == "user"` (`USER_RELEASE_REQUIRED`); T2 enforces the same rule.
4. **The Main's idempotency key** (decision 2026-10-20, the Main's saved SDK session): `apply(diff, author, session_id?, tool_use_id?)`, key `sha256(session_id‖tool_use_id)`; there is no `turn_id` / `call_id` any more.

**Tests:** the existing Validator and Diff Engine suites with the new `SeedFrom` shape; an `approval` edit is affected; the `USER_RELEASE_REQUIRED` refusal; the A→New→B example with `approval: after` on A.

---

# Plan 9 — Claude Agent SDK Engine, Session Store and Model Gateway (LiteLLM)

**Owner:** Person 4 (Agent engine and models), except Phase 9.6, `ask_user` (Phase 9.3 item 3) and Phase 9.7: Person 2 (Agents, subagents and segmentation). See [team-plan.md](team-plan.md).

**Goal:** real LLM execution for every agent through the Claude Agent SDK (Python, `claude-agent-sdk`), with every model reached through LiteLLM (decided 2026-10-19). Agent nodes keep a saved SDK session per run, stored through the Application API (Appendix P D27), so a retry continues instead of reasoning again. An agent that asks the user, a mandatory tool approval and a pause suspend the attempt instead of holding a process. Internal agents are SDK subagents. The Main runs on the same engine with its own saved session (D21); the Composer and the Recovery Agent are its subagents. Every feature the SDK has (compaction, subagents, skills, questions, permissions, limits, progress, usage) is configured here, never rebuilt.

**Where the SDK sits:** the SDK is the loop of one agent (model call → tool calls → results → repeat), with context management, compaction, hooks, subagents and saved sessions. It never decides what runs next in the session; the Executor does. Its tools are CUBE.AI tools served from our process, so every call still goes through `dispatch.invoke` and the Guardrails.

**What the SDK does, and what we only configure:**

| Need | SDK feature | Our part |
|---|---|---|
| The agent loop, parallel read-only tools | the loop, `readOnlyHint` | the tool bridge (item 1 of 9.4) |
| Context that outgrows the window | automatic compaction, prompt caching | nothing (the Main included) |
| Memory across restarts, retries, reruns from a point | saved sessions, `resume`, `fork_session`, `resume_session_at` | `HttpSessionStore` (9.5) |
| Helper agents, parallel helpers | subagents (`agents=`, `AgentDefinition`) | build the definitions from `agent.md` (9.6) |
| Guidance loaded on demand | skills (`plugins=`, `skills=`) | the `cubeai` plugin (3.5) |
| A question to the user | `AskUserQuestion`, `can_use_tool`, PreToolUse `defer` | suspend and resume the attempt (Plan 10) |
| Blocking or approving a tool | PreToolUse hooks, `disallowed_tools`, the session's tool list | the Guardrails rules that are ours (Plan 11) |
| Limits | `max_turns`, `max_budget_usd`, `effort` | values from `agent.md` and the profiles |
| Progress steps | `TaskCreate` / `TaskUpdate` | turn them into canvas events (9.4 item 7) |
| Usage and cost | `ResultMessage.model_usage`, `total_cost_usd` | write them on the attempt (9.2 item 6) |
| Filtering a tool result before the model sees it | PostToolUse `updatedToolOutput` | the PHI rule (Plan 13.3) |
| Final output with a schema | `output_format` | the agent's `output_contract` |

Not in the SDK, so ours: the session-wide wall-clock timeout (the SDK has none), the durable inbox, the graph, the Executor, the job platform, LiteLLM, the database.

**References** (read 2026-10-19): agent loop <https://code.claude.com/docs/en/agent-sdk/agent-loop> · sessions <https://code.claude.com/docs/en/agent-sdk/sessions> · session storage <https://code.claude.com/docs/en/agent-sdk/session-storage> · hooks <https://code.claude.com/docs/en/agent-sdk/hooks> and "Defer a tool call for later" in <https://code.claude.com/docs/en/hooks> · subagents <https://code.claude.com/docs/en/agent-sdk/subagents> · Python reference <https://code.claude.com/docs/en/agent-sdk/python> · gateways <https://code.claude.com/docs/en/llm-gateway-protocol> · LiteLLM <https://docs.litellm.ai/docs/anthropic_unified>.

## Phase 9.0 — Spike (verify before building)

A short, throwaway check against the pinned SDK version and a local LiteLLM. These behaviours decide the design, and not all of them are documented for the Python SDK:

1. `disallowed_tools` with the built-in names, plus `strict_mcp_config=True`, `setting_sources=[]` and `CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS=1`, leaves the model with only our MCP tools (and `Agent` only when the spec has delegates).
2. A PreToolUse `defer` from a Python hook ends the turn with `stop_reason: "tool_deferred"` and `deferred_tool_use`. Check how a resumed `query()` re-fires the hook (and which prompt it needs), what happens when the deferred call was one of several parallel calls (documented: `defer` is ignored and the call goes through the normal permission flow), and whether `defer` works from inside a subagent.
3. A message sent with `ClaudeSDKClient.query()` while a turn runs reaches the agent mid-loop (streaming input).
4. `session_id=<uuid>`, `resume`, `fork_session` and `resume_session_at` work with `session_store` set; `run_session_store_conformance` passes for `HttpSessionStore`; what a `mirror_error` message carries.
5. Whether the Python SDK can set a compaction threshold per session. If it cannot, `max_context_tokens` is dropped from `agent.md` (Plan 3).
6. Through LiteLLM: a Claude alias keeps prompt caching (`cache_read_input_tokens > 0`), structured output and adaptive thinking. An Ollama alias works with `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1`, either through LiteLLM's Ollama provider or as an Anthropic-format upstream (Ollama's own `/v1/messages`); record which of the two keeps tool use working.
7. The bundled CLI runs in the `python:3.13.16-slim` agents image on amd64 and arm64.
8. **Skills with `setting_sources=[]`:** `plugins=[{"type": "local", "path": <the cubeai plugin>}]` loads its skills (the init message lists `cubeai:<name>`); `skills=["cubeai:<name>", …]` hides every other skill, the CLI's bundled ones included; a `Skill` call reaches the PreToolUse hook; front-matter fields we do not use are ignored; and the `Skill` tool works on the local Ollama alias. Also record what a prompt that starts with `/` does (the SDK dispatches commands and skills by name).
9. **`AskUserQuestion` with a durable wait:** the PreToolUse hook `defer`s it, the turn ends with `tool_deferred`, and on resume `can_use_tool` returns `PermissionResultAllow(updated_input={questions, answers})` from the recorded answer. Check the permission mode this needs (`dontAsk` denies `AskUserQuestion` even when allowed, so the sessions that use it run in `default` mode with a `can_use_tool` that denies everything else), and that `AskUserQuestion` is not offered to subagents.
10. **The Main's saved session:** `session_id = <CUBE.AI session id>` with `session_store` set, `resume` after a restart, the SDK's compaction on a long session, and a `UserPromptSubmit` hook that adds the graph summary to each turn (`additionalContext`).
11. **Subagents of the Main:** the Main calls the `composer` subagent and waits for its answer (subagents may run in the background by default); the subagent's transcript is stored under its `subpath`.
12. **Task tools:** `TaskCreate` / `TaskUpdate` are offered when named in `allowed_tools` (on every model the deployment uses, the local one included), and their calls appear in the message stream.
13. **Limits:** `max_budget_usd` ends a run with `error_max_budget_usd`, and counts subagents.

The findings are written into this plan before Phase 9.4 starts.

## Phase 9.1 — Dependencies and runtime

- Add `claude-agent-sdk` (pinned exactly; it bundles the Claude Code CLI) to `Backend/agent_system/requirements.txt` (UTF-16 LE, CRLF) and `deploy/Dockerfile.agents`. Add `anthropic` (pinned) for the plain Messages calls of Phase 9.7. **No** `langgraph`, `langgraph-*` or `langchain-*` package.
- **The CLI's files:** every SDK session runs with `CLAUDE_CONFIG_DIR` set to a fresh per-session directory under `storage_paths` (`temp/`), deleted when the session ends, and with one fixed working directory (`storage_paths`, `cache/agent_cwd/`), so the store key (`project_key`, derived from the working directory) is the same for every run. `install_write_guard()` allows these two trees. Non-essential traffic and telemetry are off (`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`).
- **One CLI subprocess per running SDK session.** The `llm` pool (`config/resources.yaml`) bounds how many agent nodes run at once. The Main sessions (with their Composer and Recovery subagents) are counted against a separate `llm_control` pool, so a busy graph never blocks the chat.
- **Upgrading the SDK** while runs are `suspended` or `interrupted` is a release step: the transcript format belongs to the SDK, so drain them first, or accept that a resume can fail (`transcript_incompatible`, Phase 9.4) and the node is rerun. Every run freezes the version it started with (`execution_policy.engine_version`) and its spec digest (`execution_policy.spec_digest`: SHA-256 of the agent's prompt, tool names, delegates and `version`).

## Phase 9.2 — Model Gateway on LiteLLM (`harness/model_gateway.py`, `config/models.yaml`, `deploy/litellm/config.yaml`)

1. **The LiteLLM service** (`deploy/docker-compose.backend.gpu.yml`): a pinned `litellm` image started with `deploy/litellm/config.yaml`, a `LITELLM_MASTER_KEY`, and **no database** (no spend UI and no virtual keys; usage comes from the SDK, item 6). Only this service gets the provider credentials (`ANTHROPIC_API_KEY`, cloud credentials, `OLLAMA_API_BASE`). The agents service gets `LITELLM_BASE_URL` and `LITELLM_API_KEY`. The rule "only the Application API gets `DATABASE_URL`" is unchanged.
2. **`deploy/litellm/config.yaml`:** one `model_list` entry per model alias the system may use (for example `cubeai-claude-main`, `cubeai-claude-agent`, `cubeai-local-agent`), each with its upstream (`anthropic/…`, `bedrock/…`, `vertex_ai/…`, or the Ollama form the spike chose). Fallbacks between aliases are LiteLLM router fallbacks. The proxy must pass `anthropic-beta`, `anthropic-version` and `cache_control` through unchanged, stream without buffering (SSE `ping` events included), and return upstream error bodies unchanged. `/v1/messages/count_tokens` is optional.
3. **Profiles** (`config/models.yaml`): `main`, `composer`, `recovery`, `agent_default` and `extraction` (cohort labels). There is no `summarizer` profile: the SDK compacts every session on the session's own model. Each profile names `{alias, fallback_alias?, locality: local|external, claude_native: bool, effort?}`. Which models the aliases point at is configuration, not plan.
4. **`options_for(profile, ctx) -> ModelOptions{model, fallback_model, effort, env}`** builds what an SDK session needs:
   - `env`: `ANTHROPIC_BASE_URL = LITELLM_BASE_URL`, `ANTHROPIC_AUTH_TOKEN = LITELLM_API_KEY`, and `ANTHROPIC_DEFAULT_HAIKU_MODEL` = the same alias, so the CLI's background requests (titles, summaries) never reach another model. For `claude_native: false`, also `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` and `CLAUDE_CODE_ATTRIBUTION_HEADER=0`;
   - `fallback_model` from `fallback_alias`, which must have the same locality;
   - the `locality_filter(ctx)` hook: empty now; Plan 13 fills it for PHI-Local (only `local` aliases).
5. **User settings:** `/client-config` (and `ClientSettingsPanel`) picks a LiteLLM alias per profile; it no longer holds base URLs or keys. `client_config.py` keeps its retry delays (`LLM_RETRY_DELAYS_SECONDS`) for the plain Messages calls of Phase 9.7. The panel follows in the same change (`team-plan.md`).
6. **Usage:** each SDK session's `ResultMessage.model_usage` (subagents included) is the attempt's usage. It is written once per attempt (`run_attempts.usage`, T5/T6/T7) and once per Main turn (T8, the Composer and Recovery subagents included), and added to `sessions.tokens_used` there. The budgets in Plan 11 use the persisted total plus the running sessions' counters.
7. **Known limits, written in the docs:** Anthropic does not support routing Claude Code to non-Claude models through a gateway, so tool use on a local model is measured in the `agent-regression` suite before a local alias serves an agent. Sessions that go through a gateway are not eligible for Anthropic's HIPAA configuration (Plan 13).

## Phase 9.3 — Toolkit contracts for the engine (Plan 1 follow-up)

These extend Plan 1 contracts. `toolkit/` still imports nothing from the SDK (`test_boundary.py`, with `claude_agent_sdk` added to its list): the engine plugs its behaviour in.

1. **`AgentStatus.SUSPENDED`** and `AgentResult.suspension = Suspension(kind: ask_user|tool_approval|pause, tool_use_id?, payload)`. A suspended result has a `suspension` and no `error_code`.
2. **`AgentEngine.run(spec, task, inputs, ctx, cancel=None, resume: Resume | None = None)`.** `Resume(value)` answers the deferred call of a suspended run (an `ask_user` answer, or a tool-approval decision); `Resume.continue_()` continues an interrupted or paused run from its saved session. `FakeAgentEngine` records `resume` and can script a suspension.
3. **Questions to the user.** A free question is the SDK's built-in **`AskUserQuestion`** (1–4 multiple-choice questions, with free text as an answer); nothing of ours is built for it. Only the two **domain forms** need a tool of ours: **`ask_user(form, payload)`** (`toolpacks/interaction/ask_user.py`, `visibility={"agent"}`, inline, light), where `form` is `cohort_approval` or `segmentation_review` and the payload holds refs and summaries only; the UI renders the form. In both cases the asking is the PreToolUse hook's `defer` (Phase 9.4), the suspension's `kind` is `ask_user`, and the answer comes back on resume. The Main does not ask through these: it simply asks in the chat.
4. **Removed:** `ToolContext.user_gate()`; `ToolContext.durable()` / `durable_map()` (a tool that works in parts records each finished part in its domain rows and continues from them on a re-call, Definition of Ready item 13); and `Dispatcher(passthrough=…)` (no engine exception crosses dispatch any more).
5. **The live part of `ToolContext`** gets an `engine_hooks` field next to `event_sink` (the session's pause flag and the node's mailbox). It is left out of `to_payload()`, like the other live fields.
6. **The Main's long-lived session:** `open_main(spec, ctx) -> MainSession` (starts the session with `session_id` = the CUBE.AI session id, or resumes it from the store) and `turn(session, message, cancel) -> AgentResult`, next to `run()` on the engine (the Main keeps one `ClaudeSDKClient` across turns, Plan 12.4). `FakeAgentEngine` gets the same two methods.

## Phase 9.4 — `ClaudeAgentEngine` (`toolkit/engines/claude_engine.py`, `options.py`, `mcp_bridge.py`, `hooks.py`)

`ClaudeAgentEngine` implements `AgentEngine`. One `run()` is one SDK session (a `ClaudeSDKClient`), started, resumed or forked as in item 4.

1. **The tool bridge (`mcp_bridge.py`):** for each run, one in-process MCP server (`create_sdk_mcp_server("cubeai", tools=…)`) built from `spec.tools_for(ctx)`. Each SDK tool has the catalog tool's name, description and `input_model` JSON schema, and a handler that calls `dispatch.invoke(spec, args, ctx_for_call, cancel)` with the run's bound `ToolContext` (a subagent's calls carry `subagent=<type>`, taken from the hook input). Read-only tools set `readOnlyHint`, so the SDK runs them concurrently. The model sees `mcp__cubeai__<name>`; prompts and `allowed_tools` keep the catalog names, and the bridge maps them. Results go back as the tool returned them: small refs, ids and summaries (Definition of Ready item 14).
2. **The options (`options.py`):**
   - `system_prompt` = the spec's prompt; `model`, `fallback_model`, `effort` and `env` from the Model Gateway; `mcp_servers={"cubeai": …}`;
   - `allowed_tools` = the mapped tool names, **except** `ask_user` and every tool a mandatory approval may hold, which must reach the hook and the permission callback; plus `TaskCreate` and `TaskUpdate` for execution agents (naming them is also what turns them on);
   - **the SDK built-ins a spec may use:** `Agent` when it has `delegates` (the Main, segmentation, literature), with `agents=` built from them (Phase 9.6); `Skill` when it has `skills`, with `plugins=[{"type": "local", "path": <agent_system/skills/cubeai>}]` and `skills=["cubeai:<name>", …]`; `AskUserQuestion` for execution agents;
   - `disallowed_tools` = every other built-in tool (`Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, `NotebookEdit`, `ToolSearch`, and any of `Agent`, `Skill`, `AskUserQuestion`, `TaskCreate`, `TaskUpdate` the spec does not use), plus `Agent(fork)` always;
   - `permission_mode="dontAsk"` for sessions without `AskUserQuestion`; `"default"` with a `can_use_tool` that denies everything except a resumed `AskUserQuestion` for execution agents (Phase 9.0 item 9); `strict_mcp_config=True`, `setting_sources=[]`, `CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS=1`;
   - `max_turns` from `max_tool_iterations`; `max_budget_usd` from the spec or its profile; `effort`; `output_format` from `output_contract`; `include_partial_messages=True`;
   - for a resumable run and for the Main: `session_store = HttpSessionStore` and `session_store_flush = "eager"`;
   - `cwd` and `CLAUDE_CONFIG_DIR` from Phase 9.1.
3. **The hooks (`hooks.py`)**, which run in our process:
   - **PreToolUse:** builds a `ToolCall` action and runs `guardrails.check()` (Plan 11). `allowed` → allow; `permission_denied`, `invalid` or `resource_unavailable` → `deny`, with the code as the reason; `approval_required` inside a node → `defer` (a `tool_approval` suspension). `ask_user` and `AskUserQuestion` → `defer` (an `ask_user` suspension). On a resumed run the hook fires again for the deferred call and returns `allow` with `updatedInput` carrying the recorded answer (for `AskUserQuestion`, `can_use_tool` returns the `answers`), or `deny` with `approval_rejected`. `Skill` → `allow` for a listed skill. The SDK built-ins of the previous item need no Guardrails check of ours. Before it allows a call, it records the call in the attempt's `pending_tools` (Phase 10.1).
   - **`can_use_tool`** is reached only when `defer` was ignored because the model made parallel calls. It denies the call with "call `ask_user` (or this tool) alone in its turn".
   - **PostToolUse:** removes the call from `pending_tools`. For a session whose model is external, it replaces a result marked `phi: true` with its refs and non-PHI summary (`updatedToolOutput`, Plan 13.3). When the session is pausing, it interrupts the session at this boundary (Phase 10.6).
   - **UserPromptSubmit** (the Main only): adds the current graph summary and the pending approvals to each turn (`additionalContext`, Plan 12.3).
   - **Stop:** runs `completion_check`. While it returns issues and `completion_retries < max_completion_retries`, it blocks the stop with the issues as the reason, so the agent continues.
   - **PreCompact, SubagentStart, SubagentStop:** publish the ephemeral events `agent_compacting`, `subagent_started` and `subagent_finished`.
4. **Saved sessions** (a `resumable` spec with a run in its context):
   - attempt 1 starts with `session_id = run_id`;
   - a retry attempt resumes it (`resume = run_id`);
   - a seeded first start (`seed_from = {run_id, message_uuid}`, Appendix P D27) runs with `resume = seed.run_id`, `fork_session=True`, `session_id = run_id` and `resume_session_at = seed.message_uuid`.

   The Main uses the same store with `session_id` = the CUBE.AI session id (`resume` after a restart or an idle stop). The internal agents that code calls (the segmentation module) run with no store and with `CLAUDE_CODE_SKIP_PROMPT_HISTORY=1`.
5. **The outcome of `run()`**, read from the `ResultMessage`:
   - `stop_reason == "tool_deferred"` → `SUSPENDED`, with the `deferred_tool_use` as the suspension (`ask_user` or `tool_approval`); the Node Runner runs T10 suspend (Plan 10);
   - interrupted for a pause → `SUSPENDED(kind=pause)`;
   - `success` → `COMPLETED`, with `structured_output` (validated against `output_contract`) or the final text;
   - `error_max_turns` → `FAILED(max_iterations)`; `error_max_budget_usd` → `FAILED(budget_exceeded)`; `error_max_structured_output_retries` → `FAILED(bad_output)`; `error_during_execution` → `FAILED(error)`;
   - a resume whose `spec_digest` or `engine_version` differs from the run's frozen policy, or that the SDK cannot load → `FAILED(transcript_incompatible)`. That failure goes to the inbox, and the fix is a rerun (a new run starts a new session);
   - `resume=Resume(value)` → `resume = run_id`, and the hook answers the deferred call; `resume=Resume.continue_()` → `resume = run_id` with a short "continue" message.
6. **Mirror failures:** the SDK retries a failed `append` three times, then drops the batch and emits `mirror_error` without stopping. The engine treats the first `mirror_error` as fatal for the attempt: it interrupts the session and returns `FAILED(transcript_write_failed)`. A fencing refusal (`StaleAttempt`: the attempt was superseded or cancelled) is handled the same way. Such a run is never resumed automatically (Appendix P T4 rule (f)).
7. **Streaming and progress:** `StreamEvent`s become ephemeral `stream_chunk` events (the Main Agent). `TaskCreate` / `TaskUpdate` calls of an execution agent become ephemeral `agent_task` events (the canvas shows them as the node's steps). Tool progress keeps using the ToolContext event sink (`emit_progress` → `tool_progress`). Every message the engine receives is a heartbeat for the Watchdog (Phase 10.5). Messages from a subagent carry `parent_tool_use_id` and are tagged with `subagent`.
8. **Cancel:** the cancel token calls `client.interrupt()` and reaches `dispatch.invoke` for a running tool, so a running job is cancelled (Plan 5). A cancelled run is never resumed automatically.
9. **The Main:** one long-lived `ClaudeSDKClient` per `SessionRuntime` (Plan 12.4), with its saved session (item 4) and the SDK's compaction. The Composer and the Recovery Agent are its SDK subagents (`agents=`), with their own model alias, skills and tools. Graph-change approvals stay harness approvals (D7) and never use `defer`.

## Phase 9.5 — Session store (`execution/session_store.py`)

1. **`HttpSessionStore`** implements the SDK's `SessionStore` protocol over the Application API (Phase 6.6) and never touches PostgreSQL (D1):
   - two kinds, built by the engine: `HttpSessionStore.for_run(run_id, attempt_id)` for an agent node and `HttpSessionStore.for_main(session_id)` for the Main;
   - `append(key, entries)` → `POST /runs/{run_id}/transcript` on the attempt path (`X-Attempt-Id` of the run's current attempt), or `POST /sessions/{session_id}/main-transcript` for the Main. `key.session_id` is the `run_id` or the session id; `key.subpath` (a subagent's transcript) is stored as given; entries are opaque JSON, deduplicated by `entry.uuid`;
   - `load(key)` → the matching `GET` (the entries in order, or `None`); `list_subkeys(key)` → the subpaths, so subagent transcripts come back on resume;
   - `list_sessions`, `list_session_summaries` and `delete` are not implemented: deletion is the retention purge only;
   - `key.project_key` must equal the fixed one (Phase 9.1); anything else is refused.
2. **Fencing:** a refused write raises `StaleAttempt`, and the engine stops the attempt (Phase 9.4 item 6).
3. **Seeds:** `load` of another run's transcript is allowed only for the seed source named by the current run's frozen `seed_from` (Appendix P T4).
4. **Retention:** the Agent System calls `POST /agent-transcripts/purge` every `AGENT_TRANSCRIPT_PURGE_S` (wired in Plan 17).
5. **Reads for people and for the Recovery Agent** (the step timeline of a run) go through the SDK's own helpers over this store (`get_session_messages_from_store()`, `get_subagent_messages_from_store()`), never by parsing entries (global rules).

**Tests:** the SDK's conformance suite (`claude_agent_sdk.testing.run_session_store_conformance`) runs against `HttpSessionStore` (respx, fake Application API) and against an in-memory store backed by `InMemoryGraphStore`; plus the fencing refusal, the seed read rule and the project-key refusal.

## Phase 9.6 — Subagents and parallel work inside a node

1. **Internal agents are SDK subagents.** For a spec with `delegates`, the engine passes `agents={name: AgentDefinition(description, prompt, tools=[mapped names], model=<its profile's alias>, effort?)}`, built from each internal `AgentSpec`, and allows the `Agent` tool. Their transcripts are stored with the parent's, under `subpath`. Limits per session: `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1`, and `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` from the `llm` pool size (`config/resources.yaml`), so one node cannot flood a model.
2. **`run_agent` is removed:** `delegation/run_agent.py`, its tool and its tests. `catalog.validate()` keeps the delegate rules (every delegate is `internal`; no cycles).
3. **The literature review** is an ordinary execution agent (its first version has no `delegates`, Phase 3.2; the subagents follow). Its tools are the 4 search tools, `score_paper_relevance` and `save_literature_output`; its delegates are `pico_analyst`, `query_builder`, `search_executor`, `relevance_ranker` and `context_compiler`. It decides how to search, which subagents to use and when it is done; nothing fixes the order.
4. **Parallelism inside a node** is the SDK's own: read-only tools run concurrently, and several subagents may run at once. The Executor still runs one node at a time (D5).
5. **The Main's subagents:** `composer` and `recovery_agent` (Phase 3.4) are passed to the Main's session the same way. The Main waits for their answer (its prompt says so; the SDK may otherwise run a subagent in the background). `delegation/agent_as_tool.py` is removed with its tests.

## Phase 9.7 — Engine consumers inside tools

1. **The segmentation module:**
   - `module.py:96-133` (which builds AutoGen agents from the MCP workbench) and `:1375-1376` now call `AgentEngine.run("clinical_target_intent_agent" | "mask_resolution_agent", ...)`: one-shot calls with `output_format` and no saved session (the module calls them from code, so they are not subagents);
   - `planner.py` keeps its prompts and parsing, and loses its `AssistantAgent` factories;
   - **the review:** the pair work records each pair on `segmentation_pairs`, as today, so a re-call continues from the pairs already done. When the user asked to review the masks, the segmentation agent asks with `ask_user(form="segmentation_review", …)` after the tool returns. Otherwise the batch is accepted automatically (`review_status = 'auto_accepted'`, each pair `review_decision = 'auto'`). `_wait_for_review` gets no new caller.
2. **The population tools:** cohort creation records the cohort, as today. When the user asked to approve it, the population agent asks with `ask_user(form="cohort_approval", …)`, and the approval route freezes the cohort, as today. Otherwise the agent's last step freezes it automatically (`frozen_by = 'auto'`). Either way the cohort is frozen before the node completes (D25).
3. **`cohort_labels.py`:** its LLM calls use the Model Gateway's `extraction` profile as plain Messages calls through LiteLLM (the `anthropic` SDK with `base_url = LITELLM_BASE_URL` and the profile's alias), not one CLI session per patient. Each patient's labels are recorded when they are done, so a re-call labels only the patients still missing. `common/llm.py` moves onto the gateway.

**Tests (`tests/unit/engines/`; the ones that start the CLI carry the `claude_cli` marker)** run the real SDK against a scripted fake Anthropic Messages server on `127.0.0.1` (SSE streaming, scripted tool calls), with no network:

- the tool-call path through the bridge and dispatch (events, error envelope); no built-in tool is offered;
- the iteration cap; the Stop hook's completion retries; `output_format` parsing;
- **resume:** stop the session after tool call N; a new `run(resume=Resume.continue_())` finishes without calling tools 1..N again, and a call that was running is reported to the model as cut off, not re-run;
- **questions:** `AskUserQuestion` and `ask_user(form=…)` return `SUSPENDED`; `Resume(answer)` continues and the agent receives the answer; parallel calls are refused through `can_use_tool`; the Main has neither tool;
- **tool approval:** a guardrail `approval_required` suspends; approve runs the call once on resume; reject returns `approval_rejected`;
- **pause:** a pause request interrupts at the next tool boundary and resumes with a continue message;
- **subagents:** the literature agent with 3 sources and 5 papers on fake models; a subagent's transcript is stored under its subpath and comes back on resume; the depth and concurrency limits hold;
- `seed_from` forks from the named message; `mirror_error` and `StaleAttempt` stop the attempt;
- the Main resumes its saved session after a simulated restart, and a long Main session is compacted by the SDK; the Main calls the `composer` subagent and gets a `proposal_id`; skills load only when listed; `TaskCreate` / `TaskUpdate` become `agent_task` events; `max_budget_usd` ends a run as `budget_exceeded`;
- cancel stops a running tool; `fallback_model` on a model error; usage from `model_usage`, subagents included;
- the Model Gateway: the env per profile, the `claude_native: false` flags, the pinned background model, the `locality_filter` hook;
- the segmentation module calls the internal agents through the engine.

**Done when:** every `AgentSpec` runs on `ClaudeAgentEngine` against the fake Messages server; every `resumable` execution agent passes a resume test; the question, tool-approval, pause, subagent, skill and Main-resume tests pass; the session store passes the SDK conformance suite; and `FakeAgentEngine` still passes the Plan 1 tests.

---

# Plan 10 — Executor, Node Runner, Watchdog

**Owner:** Person 3 (Execution and session state). See [team-plan.md](team-plan.md).

**Goal:** the only component that runs work (system §11-§14, §24), over the persistence of Appendix P. It starts nodes with T4, completes them with T6, cancels with T7, reconciles with T5, and suspends and resumes agent nodes with T10. It absorbs `RunHarness` and `DirectToolExecutor` from tools_plan.

## Phase 10.1 — Node Runner (`execution/node_runner.py`)

`run(node)`:
1. `catalog.resolve(node.ref)`. Resolve the inputs with `planning/inputs.resolve_inputs` and pin them: for each port, the current completed run of the selected live upstream node, with the ref from its `outputs`.
2. **Start (T4):** `GraphStore.start_node(node_id, accepted_by_change_id, resolved_inputs, prompt?, spec_digest?)` creates the run (lazily, D8) and attempt 1, or a retry attempt. The run gets a frozen `execution_policy` (the manifest limits, `resumable`, `gate_timeout_s`, `seed_from` from the accepting change, `spec_digest`, `engine_version`, plus the session's `privacy_mode`) and its `target_version`. A refusal (not ready, a stale definition, another active attempt, `NEEDS_RELEASE`) makes the Executor re-read the graph.
3. **Context:** a `ToolContext` with `run_id` and `attempt_id`. Every write the agent or the tool makes presents the D24 attempt path.
4. **Agent Node:**
   - mark the attempt `running` (with `dispatched_at`) before spawning (there is no register tool: the run's lifecycle is T4–T6);
   - `engine.run(spec, task=node.params.task, inputs, ctx, cancel)`, with a **"Node inputs"** section rendered from the pinned summaries. The engine starts the run's SDK session (`session_id = run_id`), or forks the `seed_from` source on attempt 1 of a seeded run (Plan 9.4);
   - **a retry attempt of a `resumable` run** calls `engine.run(..., resume=Resume.continue_())`, so it resumes the run's saved session. With nothing saved yet, it starts normally;
   - the PreToolUse / PostToolUse hooks keep `run_attempts.pending_tools` current (`set_pending_tools`), so T4 rule (f) knows which call was running if the process dies;
   - the Stop hook runs `completion_check` (Plan 9.4).
4a. **Suspend and resume (T10, agent nodes only):**
   - `engine.run` returns `SUSPENDED` (an `ask_user` question, a tool approval, or a pause) → `suspend_attempt(suspension)`: the attempt becomes `suspended`, its `llm` pool slot is released, the SDK session ends, and the task ends. No process waits, and nothing is held in memory: the transcript is in the Application API;
   - the user's answer (`gate_resolved`) or the end of a pause wakes the Executor (Phase 10.2), which calls `resume_attempt` and then `engine.run(..., resume=Resume(value))` (for a pause: `Resume.continue_()`) with the **same** attempt. A suspension is not a new attempt (I8);
   - a fencing refusal on resume (the run was superseded or cancelled meanwhile) ends the task without running anything.
5. **Tool Node:** `dispatch.invoke(spec, params + input refs, ctx, cancel)`: inline (marked `running` first) or a job (the worker marks it `running`, Plan 5).
6. **Outputs:** call each output port's `extract` → `{type, ref:{table, ids}, summary, phi}`. For an agent node, the agent's structured output (the SDK's `output_format`, from its `output_contract`) is the input to `extract`; the Node Runner shows the agent's view with it (the view branch `write_agent_output` used to trigger, `common/views.py`) and clears the run's temp dirs (H13). **`write_agent_output` is deleted here**, with its tests.
7. **Finish (T6):**
   - success → `complete_attempt(outputs, usage)`. The Application API checks the refs, evaluates the node's expectations and may set `downstream_hold`;
   - failure → `fail_attempt(error_code, usage)`, with `error|timeout|oom|interrupted|cancelled|max_iterations|bad_output|resource_unavailable|transcript_incompatible|transcript_write_failed`.
8. **Events:**
   - the durable `node_status` and `run_attempt_*` events come from the Application API transactions;
   - the `on_result` view events come from dispatch;
   - heartbeats live in memory only and are never persisted (D19).
9. **Asking the user inside a node** (`ask_user`, a domain form such as the cohort approval, or a mandatory tool approval; only when the user asked for it, or when a guardrail requires it, D28): the attempt is suspended (step 4a), and the node shows `waiting_for_user` from the durable `run_attempt_suspended` event and the derived activity `awaiting_user`, so the wait survives a refresh and a restart. The answer is written through the D24 `user gate` path together with T10's answer step. The live events `agent_waiting_for_user_input` / `agent_waiting_resolved` are replaced by `run_attempt_suspended` / `gate_resolved`.

## Phase 10.2 — Executor loop (`execution/executor.py`)

There is one `SessionExecutor` task per session with work. It is sequential, which is a current-phase constraint:

```
while True:
    node = graph.next_ready()     # previous live node's current run completed, no active downstream_hold
                                  # (expectation or user_review), an approval:before node released,
                                  # session active, no non-terminal attempt in the session
    if (s := graph.resumable_suspension()):   # a suspended attempt the user has answered, or whose pause ended
        if pools.try_acquire(s.node.resources): spawn(node_runner.resume(s))
        else: await pool_released
        continue
    if node is None:
        await next(completion | graph_changed | pause_changed | stop_confirmed | gate_resolved); continue
    decision = guardrails.check(NodeStart(node))        # budget, resources
    if decision is not allowed:
        publish(node_start_refused(node, decision))     # runtime only, nothing stored (D19)
        await next(graph_changed | pool_released | probe_ok); continue
    if not pools.try_acquire(node.resources):
        await pool_released; continue
    spawn(node_runner.run(node))
```

- **Pools:** `execution/resources.py` gives each pool (`llm`, `cpu_heavy`, `gpu`, `monai_inference`, `orthanc_io`) an asyncio semaphore sized from `config/resources.yaml`. They are runtime only; the concurrency of each queue's compute worker remains the real limit for jobs (Plan 5).
- **No cache lookup:** reuse is the unaffected prefix that keeps its current runs (Appendix P §P.10).
- **Suspended attempts:** a `suspended` attempt is non-terminal, so no other node starts in the session while it waits (the session-wide index, a current-phase constraint). That is the same behaviour as a blocking gate today, but with no process. `gate_resolved` wakes the Executor, not the Main.
- **Approvals the user asked for (D28):** an `approval: before` node is not ready until a user `ReleaseHold`; when it becomes otherwise ready, the Executor publishes `approval_requested` once. An `approval: after` node's completed run holds the next node (`user_review`) until a user `ReleaseHold`. Both show as `awaiting_user`.
- **Important events** (system §22): a node fails after the automatic retries, `expectation_violated`, `graph_idle`, or an `approval_requested` for a node the user must release (the Main tells the user in the chat). A normal success is persisted and shown; it does not wake Main. The inbox reads them from the durable log (Plan 12).
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
   - **agent nodes:** an in-memory heartbeat (`NODE_HEARTBEAT_TIMEOUT_S`, fed by every message the SDK session yields) and `timeout_s` from the frozen policy, counted from the latest of `started_at` and `resumed_at`, so time spent suspended never counts;
   - **suspended attempts:** no heartbeat, because no process runs. An `ask_user` or tool-approval suspension older than the policy's `gate_timeout_s` (when set) gets a T7 cancel request with `error.code='gate_timeout'`; a pause suspension never times out;
   - **job nodes:** the `JobRecord` from `JobClient.get` (`TIMED_OUT`, `INTERRUPTED` after a stale worker heartbeat, `error_code="oom"`; Plan 5).
2. **Timeout:** a cancel request with `error.code='timeout'` (T7). The attempt becomes `failed(timeout)` only once the stop is confirmed (D9).
3. **Automatic retries** (deterministic, not LLM): a new attempt through T4, only for `interrupted` and `resource_unavailable`, only while `attempt_number < execution_policy.max_attempts`, and, for an attempt with `dispatched_at` set, only when the policy says `idempotent`. **For a `resumable` agent run**, the run counts as idempotent unless the latest attempt's `pending_tools` lists a tool with `idempotent=False`: every finished tool call is in the transcript and will not run again, and the SDK reports a cut-off call to the model instead of re-running it, so only the pending calls matter (T4 rule (f)). An attempt that failed with `transcript_write_failed` is never retried automatically. All other failures go to the inbox.

## Phase 10.6 — Control and restart

1. **Pause:** `control_state='paused'`. No new start (T4 requires `active`). A running agent node is interrupted at its next tool boundary (the PostToolUse hook calls `client.interrupt()`) and is **suspended** (`kind: pause`, T10), so a pause holds no process and survives a restart; on resume the session continues with a short "continue" message. Jobs cannot pause, so they finish or are cancelled. The activity shows `pausing` until no attempt is `preparing`, `submitted`, `running` or `unknown`.
2. **Resume:** `active` again; the Executor resumes a pause-suspended attempt (T10). **Stop:** T7 on the active attempt (confirmed at once when it is `suspended`); the run becomes `cancelled` after confirmation, and the session `idle`. A later explicit user retry of a cancelled run opens a new attempt (T4 rule b); for a `resumable` run that attempt resumes its saved session.
3. **Restart recovery** (in the app lifespan; Plan 17 wires it): T5 for every non-terminal attempt:
   - `suspended` → unchanged: nothing was running, and the transcript holds the state. It resumes when the user answers or its pause ends;
   - `in_process` / `inline` → `interrupted`. A `resumable` agent run then gets a retry attempt (Phase 10.5) that resumes its saved session instead of starting over;
   - job attempts are re-attached by `submission_key` through `JobClient.get` (the `job_execution_status` view plus the job dir), resubmitted through `JobClient.submit` when still `preparing` and unknown to the queue, and `record_lost` when the job row was purged after dispatch. A Procrastinate job is never `unknown`, because it lives in the Application API's own database; a MONAI job still can be.

   Then the Executor restarts the sessions that have ready or resumable work, and an `execution_resumed` notification is sent.

**Tests (`tests/unit/execution/`,** using `FakeAgentEngine`, the in-memory `JobClient` and `InMemoryGraphStore`):
- A→B→C readiness in order (§12); the Executor keeps running while the inbox is busy (§25);
- the pins: B consumes A's run 1 even after A is rerun in the middle;
- cancel of an affected running node; the next start waits for the confirmed stop;
- the expectation hold on the violating run, and its release by `ReleaseHold`;
- the watchdog: timeout as a cancel request, OOM, interrupted; retry only within the policy, and the `dispatched_at` rule;
- the pause; restart reconciliation for every attempt state; `node_start_refused` for a pool or budget refusal;
- **suspension:** an `ask_user` question suspends the attempt and frees its pool slot; `gate_resolved` resumes the **same** attempt; a pause suspends and the end of the pause resumes; a restart leaves `suspended` attempts unchanged and they still resume; T7 on a suspended attempt confirms at once; `gate_timeout_s` cancels with `gate_timeout`; `timeout_s` ignores suspended time;
- **approvals the user asked for:** an `approval: before` node waits for a user `ReleaseHold` and publishes one `approval_requested`; an `approval: after` node's run holds the next node until a user `ReleaseHold`;
- **resume after interruption:** an interrupted `resumable` run is retried automatically and resumes its session; it is not retried when `pending_tools` holds a non-idempotent tool, nor after `transcript_write_failed`; a seeded first attempt forks from the seed message;
- every write carries the D24 attempt path.

**Done when:** a 3-node graph runs to completion on fakes, a node that asks the user suspends and resumes across a simulated restart, and every rule above has a passing test.

---

# Plan 11 — Runtime Guardrails, Modes, Approvals

**Owner:** Phases 11.1 and 11.2: Person 5 (Main Agent and session layer). Phase 11.3: Person 3 (Execution and session state). See [team-plan.md](team-plan.md).

**Goal:** one place that decides execution boundaries (system §10). It also covers Plan/Auto modes and approvals (§31).

## Phase 11.1 — Guardrails core (`harness/guardrails.py`)

1. **The contract:**
   - `check(action, ctx) -> Decision(outcome, code, detail)`, where `outcome` ∈ `allowed | approval_required | permission_denied | invalid | resource_unavailable`;
   - action kinds: `ToolCall` (by Main or an agent), `GraphChange`, `NodeStart`, `ModelCall`.
2. **Hook points:**
   - the engine's PreToolUse hook (Plan 9.4) for every tool call an SDK session makes (the Main, the Composer, the Recovery Agent, agent nodes and their subagents), so a refusal or a `defer` happens before the tool runs and the model sees the reason;
   - `dispatch.invoke` `before_invoke` (Plan 1), for calls that do not come from an SDK session (tool nodes run by the Executor); a call already checked by the PreToolUse hook is not checked twice;
   - the Diff Engine `apply` / `propose`;
   - the Executor `NodeStart`;
   - the Model Gateway (`ModelCall`).
3. **Rules** (small functions, evaluated in order; the first non-`allowed` result wins):

   | Rule | Outcome |
   |---|---|
   | Schema / Validator errors | `invalid` (with the codes) |
   | The caller's visibility does not include the tool (only for calls that do not come from an SDK session: an SDK session only has the tools its spec lists, so the SDK enforces this) | `permission_denied` |
   | Main calls a tool directly that is not `inline`, or that is `heavy` (the same: such a tool is not in the Main's session, so this only guards `/graph/diff` and direct API calls) | `invalid: REQUIRES_NODE` |
   | A pool or queue is not alive (`GET /jobs/queues`, MONAI health) | `resource_unavailable` |
   | Budgets at `NodeStart` and `GraphChange`: the session total (`SESSION_TOKEN_BUDGET` against `sessions.tokens_used`), active nodes, `max_attempts`. Inside one SDK session the SDK enforces `max_turns` and `max_budget_usd` itself | `permission_denied: BUDGET_EXCEEDED` |
   | Mandatory approvals: `destructive=True`, `trust != first_party`, `generated_code` | `approval_required` |
   | Plan mode (Phase 11.2) | `approval_required: PLAN_MODE` |
   | PHI / privacy (Plan 13) | `permission_denied` |

4. **Test:** a unit test asserts that no rule reads the request text, counts steps, or looks at `cost` to pick a strategy.

## Phase 11.2 — Modes (Plan / Auto)

- `execution_mode` is stored on the session (`PATCH /session/{id}/settings` on the Agent System, which calls the Application API). **The default is `auto`** (decided 2026-10-01). Plan mode is opt-in, for users who want to approve every piece of work. The mandatory reasons apply in both modes.
- **Approval reasons are computed when a change is proposed** (Appendix P D7), never when a node starts:
  - `PLAN_MODE`: the session is in Plan mode and the change adds, modifies, reruns or releases work. Removing nodes that never started, and cancelling, need none. The Application API adds this reason itself;
  - `supersedes_completed`: the change would supersede a completed run (D22). The Application API adds this reason itself;
  - `destructive`, `generated_code`, `untrusted_tool`, `approval_required:<code>`: from the target manifests, computed by the guardrails here.
- A change with no reasons is applied at once (T2). A change with reasons is proposed (T1) and waits for a user decision.
- Main's direct calls to `side_effects="writes"` tools are promoted to an `AddNode` change before any effect (D21). Read-only calls are always allowed.
- Start-time conditions (budget, resources) are never approvals: the Executor refuses the start and publishes `node_start_refused` (D19).
- **Approvals the user asks for (D28, decided 2026-10-19).** They are separate from the modes and work in both. There is no fixed rule: the user may ask for an approval after every step, before every step, inside a step, or not at all. The Main carries the wish into the graph: `approval: before | after` on the nodes it submits, or a sentence in a node's `params.task` that the agent follows with `ask_user`. The harness only enforces what is set: an `approval: before` node does not start until a user `ReleaseHold`; an `approval: after` node's completed run holds the next node (`user_review`) until a user `ReleaseHold`. Only the user releases these holds (`USER_RELEASE_REQUIRED`). No guardrail reads the request text to add one (the strategy-free rule).

## Phase 11.3 — Approvals (`harness/approvals.py`)

1. **There is no `approvals` table.** The pending `graph_changes` row is the approval (Appendix P D7), and a session has at most one. A new proposal withdraws the previous one; past `APPROVAL_TTL_H` it becomes `expired`.
2. **When a change needs approval:** T1 stores it and publishes `graph_change_proposed` + `approval_requested`. The caller gets `RunAccepted{node_id, change_id, status: "awaiting_approval"}` (or the `graph_apply` result).
3. **Deciding** is done only by the user, never by an LLM, and always quotes the digest:
   - REST: `POST /session/{id}/changes/{change_id}/decision` with `{decision: approve|reject, content_digest, comment}`. The Agent System forwards it to the Application API (`/graph-changes/{change_id}/decision`);
   - deterministic chat commands, handled before the LLM in the input route: `/approve [change_id]`, `/reject [change_id] [reason]`, and the legacy text `APPROVE` when exactly one change is pending (the current canvas buttons send it). A command uses the digest of the pending change the user was shown.
4. **On approve:** T2 under CAS. If the base moved, the change becomes `stale`. **There is no auto-rebase**: Main is notified (`graph_change_stale`) and re-proposes. The change also goes `stale` if a derivable reason changed meanwhile (for example a run completed).
5. **Every decision** → a durable event and an inbox notification.
6. **Tool approvals inside a node** (D27; they replace the in-memory in-run approvals of D19): when a `ToolCall` made by an agent inside a node gets `approval_required` (a `destructive` tool, or a tool that is not first-party), the PreToolUse hook returns `defer` instead of refusing, with `{tool, args_digest, reason}` as the payload. The attempt is suspended (T10, `kind: tool_approval`) and the user decides with `POST /session/{id}/runs/{run_id}/answer` (Phase 12.7), which the Agent System forwards to the Application API. On resume the hook fires again for the same call: approve → `allow`, and the call runs once; reject → `deny` with `approval_rejected`, which the agent sees as the tool result. Like every suspension, it survives a restart. If the model made the call together with other calls, `defer` is ignored and the permission callback refuses the call with "call it alone" (Plan 9.4). Graph-change approvals (items 1–5) are unchanged and never use `defer`.

**Tests (`tests/unit/harness/test_guardrails.py`, `test_approvals.py`):**
- each rule outcome; order and short-circuit;
- a new session is in Auto mode;
- Plan vs Auto on the same change; removals allowed in Plan mode;
- mandatory reasons in Auto mode; `PLAN_MODE` and `supersedes_completed` added by the store even when the caller omits them;
- the chat commands never reach the LLM; `APPROVE` with 0, 1 or 2 pending changes;
- a wrong digest is refused; `stale` on a moved base; expiry;
- an agent's `approval_required` tool call suspends the node (`tool_approval`); approve runs the call once on resume, reject returns `approval_rejected` to the agent; a Main direct call that needs approval is still promoted to an `AddNode` change (D21), never a suspension;
- `approval: before` and `approval: after` are enforced in both modes, and only a user releases them;
- the "no strategy rule" scan.

**Done when:** every action kind passes through `check()`, and the tests pass.

---

# Plan 12 — Session Layer: Inbox, Main Loop, Context, Skills, Planner

**Owner:** Person 5 (Main Agent and session layer). See [team-plan.md](team-plan.md).

**Goal:** the Main Agent as a free, non-blocking orchestrator (system §5-§8, §23, §25, §30, §32, §33).

## Phase 12.1 — Session Inbox (`harness/inbox.py`)

1. **Item types:**
   - `UserMessage`;
   - `Notification(kind: node_failed | expectation_violated | graph_idle | approval_decided | approval_requested | execution_resumed | resource_unavailable)`; `approval_requested` is a node waiting for the user's release (D28), so the Main can tell the user in the chat;
   - `Command`.
2. **It replaces `ConnectionManager.message_queues`** (`socket_store.py:86-98,187,224`). The inbox lives in memory, but every item is durable first: `POST /session/{id}/input` calls T3 on the Application API and enqueues only after the commit (D14); notifications come from waking events in the durable log. The handled position is `sessions.inbox_handled_through_event_id`, advanced by T8 only after the reply and the changes have committed. On restart, and periodically, the inbox re-reads the waking events above the cursor.
3. **`POST /session/{id}/input`** turns `/skills`, `/approve` and `/reject` into Commands, handled without the LLM. Anything else becomes a `UserMessage`. A message that starts with `/` reaches the Main as plain text (escaped), never as the first characters of an SDK prompt, so a user can never run a CLI command (`/compact`, `/clear`, …) or a skill by name (Phase 9.0 item 8).
4. **During a Main turn:** a new item is sent into the live session with `ClaudeSDKClient.query()` (streaming input); the SDK queues it for the running turn. There is no coalescing code of ours.

## Phase 12.2 — Control tools (`toolpacks/control/`, `visibility={"main"}`, inline)

All control tools are `light`. The Composer and the Recovery Agent are not tools: they are the Main's SDK subagents (Phase 3.4).

| Tool | Does | Returns |
|---|---|---|
| `registry_search(query, kind?)` | Lexical search over `catalog.index("main")` | Compact entries |
| `registry_describe(name)` | `catalog.describe` | Schema, ports, manifest |
| `graph_inspect(node_id?)` | The graph summary, or one node's details (error, log tail, artifacts, the step timeline read from its transcript with the SDK's helpers, a pending question or approval) | Summary |
| `execute(target, params, input_sources?, expect?, approval?)` | A one-node change appended to the sequence (or inserted with `at`), through the Guardrails and the Diff Engine. `approval` is `none \| before \| after` (D28) | `RunAccepted {node_id, change_id, status: applied \| awaiting_approval}` |
| `graph_propose(diff, base_version, origin?)` | Runs the Validator on the whole target sequence and keeps the diff as a proposal (in memory, `planning/proposals.py`; nothing stored, D19). Used by the Composer and the Recovery Agent, and by the Main itself | `{proposal_id, errors, affected, approval_reasons}` |
| `graph_apply(diff \| proposal_id, base_version, on_running?)` | Applies (T2) or proposes (T1) a change | `{version, change_id, status: applied \| awaiting_approval, affected, errors}` |
| `node_cancel(node_id)` | Requests cancellation of the node's active attempt (T7); a node that never started is removed with a change | Status |
| `node_message(node_id, text)` | Adds a user interruption to an agent node: while it runs, it is sent into the live SDK session (`ClaudeSDKClient.query()`, streaming input), which records it in the transcript; while it is `suspended`, it is kept in the node's mailbox (a `chat_messages` row tagged with the `node_id`) and sent with the resume, so the message is durable either way | Ack |
| `artifact_read(node_id \| run_id, port)` | One port of a run's `outputs`: the summary and the ref (never the raw data) | Summary |

`execute` and `graph_apply` **never wait for execution**. They return as soon as the version is applied or proposed (§14).

Their idempotency key is `sha256(session_id‖tool_use_id)`, so a replayed call never submits the same work twice. Their result reaches the Main's transcript through the SDK like any tool result.

## Phase 12.3 — The Main's context (`harness/turn_context.py`)

The SDK owns the Main's context: its saved session holds the conversation, and the SDK compacts it when it grows. CUBE.AI only adds what the SDK cannot know.

1. **The system prompt** (fixed, so the SDK's prompt cache keeps it): the prompt from `main_agent/agent.md`, then the compact registry index (`catalog.index("main")`): **every** tool and agent the Main may call (`use: call`) or put in the graph (`use: node`), with its ports and cost. Knowing an entry is not calling it: only the `call` entries are SDK tools of the Main (Plan 3.4). The skills are the SDK's (Phase 3.5).
2. **Each turn:** a `UserPromptSubmit` hook adds the current graph summary (nodes as `n<node_seq>`, their status, holds) and the pending approvals and questions as `additionalContext`, so the Main always sees the graph as it is now, even after compaction.
3. **Compaction:** the SDK's own, on the Main's own model. There is no `context_summary` message, no `summarizer` profile and no rebuild from the chat.
4. **PHI:** a PHI-marked tool result never reaches an external model: the PostToolUse hook replaces it with refs and a non-PHI summary (`updatedToolOutput`, Plan 13.3).
5. **The Composer and the Recovery Agent** start from what the Main writes in the subagent call, plus their own prompt, tools and skills (the SDK's subagent context).
6. **An execution agent's context:** its own prompt, the node inputs, the node instructions (`params.task`), its own tool schemas and mailbox messages. It is built once, at the first start of the run; after that the agent's context **is** its saved SDK session, which a resume continues and the SDK compacts.

## Phase 12.4 — Main Loop (`harness/main_loop.py`, `harness/session_runtime.py`)

1. **`SessionRuntime`:** one per active session. It owns the inbox, the Main loop task, the Main's `ClaudeSDKClient` and the `SessionExecutor`. It starts lazily on the first inbox item and stops after `SESSION_IDLE_TIMEOUT_S` (which also closes the Main's client).
2. **The loop** (system §23):
   ```
   async def session_loop(rt):
       while True:
           item = await rt.inbox.get()
           if item.is_command: await handle_command(rt, item); continue
           if rt.main_client is None:                          # first item, or after a restart / idle stop
               rt.main_client = await engine.open_main(main_agent, rt.main_ctx)   # resumes the saved session
           result = await engine.turn(rt.main_client, item.as_message(), cancel=rt.turn_cancel)
           await reply(rt, result.output)     # `msg` event + persisted chat message
   ```
   The inner LLM ↔ tool loop is the Main's SDK session on `ClaudeAgentEngine` (Plan 9), saved through `HttpSessionStore.for_main` (D21).
3. **Limits:** each turn has the SDK's `max_turns` and `max_budget_usd` (from the `main` profile) and a wall-clock budget of ours. On an error, Main sends a short failure reply, and the error is logged.
4. **Main never awaits a node**, for two reasons: the control tools return at once, and a direct call to heavy work is refused (`REQUIRES_NODE`).
5. **Persistence of a turn:** the SDK saves the turn in the Main's transcript (the tool calls and results included); a call that was cut off by a restart is reported to the model by the SDK on resume. The Main's reply is also written to the chat for the UI. The turn ends with T8 (the cursor and the token usage from the turn's `ResultMessage`).

## Phase 12.5 — Planner integration

1. **The Composer is the Main's SDK subagent** (Phase 3.4); there is no `plan` tool. The Main calls it with the request and any focus nodes, and waits for its answer.
2. **The repair loop is the subagent's own:** it calls `graph_propose`, reads the Validator's errors in the result, and proposes again until there are none. It ends with the `proposal_id` (and any errors that remain).
3. **The proposal is never applied by the Composer** (it has no `graph_apply` or `execute`). The Main decides, and then calls `graph_apply(proposal_id=...)`.
4. **Removed:** `make_agent_tool`, `validate_and_repair`, `PLAN_REPAIR_ROUNDS` and `delegation/agent_as_tool.py`.

## Phase 12.6 — Skills (`harness/skills_index.py`)

- The SDK loads the skills (Phases 3.5, 9.4); there is no loader, no index in the prompt and no `skill_load` tool.
- `/skills` (a Command, no LLM) lists each `SKILL.md`'s `name` and `description` from the `cubeai` plugin, and the agents whose `agent.md` lists it. `skills_index.py` holds only this read.

## Phase 12.7 — Agent System routes and events

1. **New router `api/routes/graph.py`,** added to the `include_router` loop in `app.py` (keep `files.router` first):
   - `GET /session/{id}/graph`;
   - `POST /session/{id}/graph/diff` (user edits; it goes through the Guardrails);
   - `POST /session/{id}/changes/{change_id}/decision`;
   - `PATCH /session/{id}/settings`;
   - `POST /session/{id}/nodes/{node_id}/cancel`;
   - `POST /session/{id}/runs/{run_id}/answer` (the answer to an `AskUserQuestion` question or a tool approval; the cohort and segmentation forms keep their domain routes) → the Application API T10 answer step;
   - `GET /session/{id}/runs/{run_id}/steps` (the step timeline of the run, read from its transcript with the SDK's store helpers, for debugging, the Recovery Agent and the future canvas).
2. **Register the new events in `services/event_registry.py`:**
   - **durable** (published by the Application API transactions, registered here for classification): `user_input`, `graph_change_proposed/applied/rejected/stale/expired`, `node_run_superseded`, `run_attempt_started/ended`, `run_attempt_suspended/resumed`, `gate_resolved`, `node_status`, `expectation_violated`, `node_start_refused`, `graph_idle`, `session_state`, `approval_requested`;
   - **ephemeral:** progress keeps using `tool_progress`, and streaming keeps using `stream_chunk`; new: `agent_task` (an agent's `TaskCreate` / `TaskUpdate`), `agent_compacting`, `subagent_started`, `subagent_finished` (Plan 9.4).
3. **Update `_SOURCES`** in `tests/unit/services/test_event_registry.py` to scan `harness/`, `execution/`, `planning/` and `toolpacks/`.
4. **Progress on the canvas:** the execution view (`views/execution/utils.ts`) shows a node's `agent_task` events instead of its scratchpad notes, and **`scratchpad_write_note` is deleted** with its tests.

**Tests (`tests/unit/harness/`):**

- inbox order and dedupe; commands skip the LLM; an item that arrives during a turn reaches the live session; a restart re-reads the waking events above the cursor; T8 runs only after the reply commits;
- **a Main turn with a fake model:** `execute` returns `RunAccepted` while a fake node runs, and a second user question is answered before the node finishes (§6);
- the Composer subagent proposes until the Validator has no errors and never applies anything;
- the graph summary reaches every turn; `/skills`; a message that starts with `/` reaches the Main as plain text;
- `node_message` to a running node reaches the live session, and to a suspended node reaches the agent after resume; the Main's session is resumed from its store after a restart;
- `execute` and `graph_apply` carry `approval` into the change content; whether the Main sets it from the user's words is measured in the `agent-regression` suite, not here;
- the routes, including the answer route and the steps timeline; the event registry scan.

**Done when:** on fakes, a user message → Main → `execute` → Executor → `node_status` done → `graph_idle` notification → Main reply works in tests.

---

# Plan 13 — Recovery and Privacy

**Owner:** Phase 13.1: Person 2 (Agents, subagents and segmentation). Phase 13.2: Person 3 (Execution and session state). Phase 13.3: Person 4 (Agent engine and models). See [team-plan.md](team-plan.md).

**Goal:** the Recovery Agent, resource failure handling and PHI-Local.

## Phase 13.1 — Recovery Agent (the Main's SDK subagent `recovery_agent`)

1. **The Recovery Agent is the Main's subagent** (Phase 3.4); there is no `recover` tool. The Main calls it with the failed node.
2. **Its context:** what it reads with its tools: `graph_inspect` gives the node spec and params, the `error_code`, the log tail (the job dir or the agent run notes), the graph neighbourhood, and for an agent node the step timeline of the failed run (each step's tool calls and errors, with the message uuids, read through the SDK's store helpers). It reads summaries, never the raw transcript.
3. **What it returns:** a diagnosis, an optional `proposal_id` from `graph_propose` (e.g. new params, a smaller cohort, another model) and a message for the user. It **never applies anything** (it has no `graph_apply` or `execute`); Main decides.
   - **Time travel:** when the failure came late in a long agent node (for example a bad tool argument at step 9 of 10), the diff may be a `RerunNode(seed_from={run_id, message_uuid})` that starts the new run from the message before the mistake (the SDK forks the old session up to it, Plan 9.4), with a correction in `params.task`, instead of repeating the whole node. It is still a new run that supersedes the old one, and T4 starts fresh when the seed's transcript is gone or incompatible (D27).
4. **Order:** the deterministic retries of Phase 10.5 always run first. Only failures that remain reach Main.

## Phase 13.2 — Resource failure handling

1. **Mapping to `resource_unavailable`:**
   - a queue with no live worker (`GET /jobs/queues`, from Procrastinate's worker heartbeats, Plan 5);
   - MONAI 429 after the `unavailable_s` budget (Plan 15);
   - a GPU queue that is declared but not started;
   - Orthanc or Application API health failures.
2. **What happens:** nothing is stored on the node (D19). At start, the Executor refuses the node and publishes `node_start_refused`, and it checks again before every start. During a run, the attempt fails with `resource_unavailable`, and the Watchdog retries it through T4 within the policy. A notification goes to the inbox.
3. **Automatic retry:** the Watchdog probes the refused pools every `RESOURCE_PROBE_S` and wakes the Executor when the resource returns. This is a technical rule, not a strategy.

## Phase 13.3 — PHI-Local (`harness/privacy.py`)

1. **The setting:** `privacy_mode` (`standard|phi_local`) is set per session.
2. **In `phi_local`:**
   - the Model Gateway `locality_filter` keeps only `locality: local` LiteLLM aliases, for the session's model, its fallback, its subagents and the CLI's background model (`ANTHROPIC_DEFAULT_HAIKU_MODEL`), so no request of a `phi_local` session can leave for an external model. If there is no local alias, the Guardrails return `permission_denied: NO_LOCAL_MODEL`.
   - the LiteLLM configuration is part of the boundary: a `local` alias must point at a local upstream (Ollama in this deployment), and a contract test reads `deploy/litellm/config.yaml` and checks it.
   - `config/models.yaml` marks `gpt-oss:120b-cloud` as `external`, and ships a commented `local_default` Ollama entry.
3. **PHI labels:** output ports with `phi=true` (in `node_runs.outputs`), tool results with `phi: true`, and tools with PHI scopes mark their content. For a session whose model is external, the engine's PostToolUse hook replaces a PHI-marked tool result with its refs and non-PHI summary (`updatedToolOutput`, Plan 9.4) before the model sees it.
4. **In `standard` mode:** access is normal, as configured.
5. **Everywhere:** the audit logs hash patient identifiers.
6. **Frozen per run:** each run freezes `privacy_mode` in its `execution_policy`. Switching a session to `phi_local` requests cancellation of every active attempt frozen as `standard`, in the same transaction (T7); a `suspended` one is cancelled at once, so it can never resume under the old mode.
7. **Agent transcripts (D27):** they hold the agent's messages, so they may hold PHI. They stay in the Application API's PostgreSQL only, are never sent to an external model as raw entries, and are purged `AGENT_TRANSCRIPT_RETENTION_DAYS` after the run ends. The CLI's local copy lives only in the session's temporary `CLAUDE_CONFIG_DIR`, which is deleted when the session ends (Plan 9.1). A `seed_from` fork requires the same `privacy_mode`, and the SDK's compaction runs on the session's own (local) model.
8. **Gateway and HIPAA:** sessions that go through a gateway (LiteLLM) are not eligible for Anthropic's HIPAA configuration. If the deployment needs it for Claude, that is a deployment decision taken before any `standard` session sends PHI to Claude; `phi_local` sessions are not affected, because their models are local.

**Tests:**

- recovery returns a valid diff or none, and never applies it; a seeded rerun proposal names a message of the failed run of the same node;
- a refused start is retried when the probe succeeds; a `phi_local` switch cancels the `standard` attempts, including suspended ones; a seed across privacy modes starts fresh;
- `phi_local` with and without a local model; PHI content is filtered out of external contexts;
- `standard` mode is unchanged; audit redaction.

**Done when:** the three areas are covered by passing tests.

---

# Plan 14 — removed (no MCP edge; every tool is a CUBE.AI tool)

---

# Plan 15 — MONAI Inference Jobs + Segmentation Client

**Owner:** Person 2 (Agents, subagents and segmentation): the segmentation client (Phase 15.4) and the segmentation module (Phase 9.7) are the same code, so one person builds both. Person 1 reviews `jobs_core/backends/monai_http.py` against the `JobClient` contract. See [team-plan.md](team-plan.md).

**Goal:** fix the critical MONAI job bugs, and move the segmentation client onto the shared contract, so that the Executor can cancel it and classify its failures. MONAI keeps its own job store and GPU: it is a `JobClient` backend, not a Procrastinate queue (Plan 5).

## Phase 15.1 — Safety net

Add the first real tests for `monailabel/endpoints/inference_jobs.py`: submit, poll, resubmit after each terminal state, cancel while queued, and the restart scan. The resubmit test reproduces J1.

## Phase 15.2 — MONAI server fixes

Fix these in place. MONAI keeps its own file store, and its status strings must equal `jobs_core.status` (a contract test checks this).

- J1, J2, J4 (server side), J6, J8, J10, J11, J12. J1 is also the prerequisite of Appendix P §P.9 *Execution-adapter prerequisites*: until it lands, a failed segmentation pair cannot be re-executed within the same run.
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
- **S10 is solved by removing the wait:** the agent path no longer calls `_wait_for_review`. The tool records each pair on `segmentation_pairs` and returns; when the user asked for a review, the segmentation agent asks with `ask_user(form="segmentation_review", …)`, which suspends the node, and otherwise the batch is accepted automatically (Phase 9.7, D28). No request polls, and a restart during the review loses nothing. `_wait_for_review` is deleted with its last caller, or keeps `RetryPolicy` if a non-agent caller remains.
- The `toolpacks/segmentation/run_segmentation.py` tool also uses `MonaiHttpBackend`.

**Tests (`tests/unit/monai/`, `tests/unit/segmentation/`):**

- J1 resubmit + slot count; J2 slot kept until the work ends;
- `INTERRUPTED` handling; the TTL sweep; the status-string contract;
- backend tests with `respx`, including the 429 → `resource_unavailable` mapping;
- the heartbeat never leaks the inner task;
- a finished result is kept after a long pause;
- a node cancel reaches MONAI;
- the review: with a review requested, the node suspends on `ask_user` after the pairs finish, and a resume after a simulated restart does not resubmit any pair job and reads the review decision from the domain rows; with no review requested, the batch is `auto_accepted`.

**Done when:** the J1 test is green, both clients use `MonaiHttpBackend`, and the segmentation review happens only when the user asked for it.

---

# Plan 16 — Other Job Systems

**Owner:** Phases 16.1, 16.3, 16.4: Person 1 (Compute jobs). Phase 16.2 (MONAI training): Person 2 (Agents, subagents and segmentation). See [team-plan.md](team-plan.md).

**Goal:** fix the remaining long waits, training, ingestion and background tasks.

## Phase 16.1 — Long waits

- **S11:** the agent path no longer long-polls (Phase 15.4). If another caller of `/segmentation-review/wait` remains, the server is keyed by batch, leaks no waiters, and checks for disconnects; otherwise the endpoint is retired in Plan 17.
- **S13 (server side):** compile before taking the row lock, with `asyncio.create_subprocess_exec` in its own process group, killed on timeout.
- **S13 (client side):** `latex_to_pdf` retries and resolves the 409 `stale_version`.
- **S8:** PATCH only the changed pair.

## Phase 16.2 — Training

Fix T1–T9. T1 becomes one server-side training job (prepare → create-model → train), orchestrated by the Application API. Its receipt is a `training_jobs` row committed before the POST (Appendix P D17, transaction T9); the table is created by migration "p" in Plan 6. Training stays outside the Session Graph; it belongs to the MONAI training UI.

## Phase 16.3 — Ingestion as a job

Ingestion conversion becomes a job on the `compute.cpu` queue (Plan 5) (`job_name="ingest_convert"`, registered as a `harness`-visible job tool). This fixes G2, G3 and G6 by design. Then fix G1, G4, G5, G7 (one frontend upload client) and G8.

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

**Owner:** Phase 17.1: Person 5, with Person 3. Phases 17.2–17.6: everyone, each for the items of their own area. See [team-plan.md](team-plan.md).

**Goal:** the harness becomes the only execution path, AutoGen is deleted, and the docs describe the new system.

## Phase 17.1 — API cut-over (Agent System)

1. **App lifespan (`app.py`):**
   - build the catalog and run `validate()` (a failure stops start-up);
   - build `ClaudeAgentEngine` (with `HttpSessionStore` once agent memory lands); check that LiteLLM answers (a failure is logged and the session's activity shows `blocked`, it does not stop start-up); once agent memory lands, start the transcript purge sweep (`AGENT_TRANSCRIPT_PURGE_S`, Phase 9.5);
   - start `SessionRuntimeManager` (restart recovery, Phase 10.6, including resuming the `suspended` attempts the user has answered);
   - keep `ensure_storage_layout()` and `install_write_guard()` after every import.
2. **`POST /session/{id}/input`** → the Session Inbox (Plan 12).
3. **`/pause`, `/resume`, `/stop`:** the same paths and bodies, now driving `SessionExecutor` control (Phase 10.6).
4. **Rebuilt from graph state:** `GET /status/{id}`, `/session/{id}/execution-state` and `/session/{id}/recover`.
5. **Retired, returning `410 Gone` with a short message:** `/plan`, `/run`, `/session/{id}/resume-pipeline`. The canvas and the chat sidebar stop calling them in the same change (`team-plan.md`).
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
- No `celery`, `redis` or `fakeredis` import or requirement anywhere in `Backend/` or `deploy/`, and no `redis` or `compute-beat` service.
- No backend caller of `/plan` or `/run`, and no `Backend/mcp` in `deploy/`, `scripts/` or the launchers.
- No retired schema name (the rename map of Appendix P §P.2) outside migrations, old durable events and the problem catalog. The Phase 6.1 source scan stays in the deterministic suite.
- No `langgraph` or `langchain` import or requirement anywhere in `Backend/` or `deploy/`.
- No `claude_agent_sdk` import outside `toolkit/engines/` and `execution/session_store.py` (`test_boundary.py`); no `SessionStore` implementation other than `HttpSessionStore` outside tests (D1, D27).
- Nothing that rebuilds an SDK feature: no `make_agent_tool`, `validate_and_repair`, `delegation/` package, `context_summary` message, `summarizer` profile, `skill_load` tool or skills loader index.
- No SDK session built without the options of Plan 9.4 (only the allowed built-ins, `strict_mcp_config`, `setting_sources=[]`): one options builder, `toolkit/engines/options.py`, and a source scan that finds no other `ClaudeAgentOptions(` call.
- No provider key or provider base URL read in `agent_system/` (only `LITELLM_BASE_URL` / `LITELLM_API_KEY`); no `run_agent`, `user_gate`, `durable(` or `durable_map(` left.
- No caller of `_wait_for_review` or `/segmentation-review/wait` on the agent path, and no `agent_waiting_for_user_input` emitter (replaced by `run_attempt_suspended`).

## Phase 17.5 — Docs

1. **`CLAUDE.md`:**
   - the service table (the compute workers on Procrastinate and the `litellm` proxy; no Redis, no beat) and the `JOBS_DATABASE_URL` exception to the database rule;
   - the "Agent pipeline" section, rewritten as the harness (Main Agent, Session Graph, Executor, Guardrails, modes);
   - the Claude Agent SDK engine: one SDK session per agent-node run (`session_id = run_id`, `HttpSessionStore`), `AskUserQuestion` / `ask_user` forms / tool approvals / pause as suspended attempts, subagents for internal agents and for the Main's Composer and Recovery Agent, SDK skills, the options every session must use, the Main's saved session, `seed_from` as a fork, and the table of what the SDK does versus what CUBE.AI only configures (Plan 9);
   - LiteLLM: the service, the aliases, the profiles, the `claude_native` flags, and the known limits (non-Claude models unsupported by Anthropic through a gateway, no HIPAA configuration through a gateway);
   - approvals from the user's words (D28): `approval: before | after`, `ask_user`, the optional cohort approval and segmentation review;
   - the Toolkit (`ToolSpec` / `AgentSpec`, packs, catalog, `agent.md`, skills);
   - the new env vars;
   - the modeling engine (scikit-learn, no PyCaret) and the renamed modeling tools (Python 3.13 itself is documented in Plan 0);
   - replace the "no GPU lease / no `WAITING_FOR_RESOURCE`" note with the runtime resource pools and `node_start_refused` (nothing about resources is stored);
   - the single-worker rule still holds.
   - the database: the Appendix P vocabulary, the D24 write paths, the transactions T0–T10, the agent transcripts and suspended attempts (D27), the node approvals (D28), and the current-phase constraints;
2. **`cubeai-docs/`:**
   - `agent-system/harness.md` (the architecture above);
   - `agent-system/agent-transcripts.md`: saved sessions, `ask_user`, tool approvals, pause, resume, `seed_from`, retention, and the SDK upgrade procedure (Phase 9.1);
   - `agent-system/models.md`: LiteLLM, the aliases and profiles, and how to add a local model;
   - update `cubeai-docs/architecture/agent-harness-claude-sdk.md` to what was built;
   - `jobs/README.md` (the runbook);
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
13. **Work in parts and asking (D27, D28):** a tool never waits for a person; asking is the agent's `ask_user` call. A tool that works in parts (per patient, per pair, per paper) records each finished part in its domain rows and, when called again for the same run, continues from the parts already recorded. It has a test that stops it halfway and checks that a re-call does only the rest.
14. **Results in messages:** what a tool returns to the model is small (refs, ids, summaries), because it is stored in the agent's transcript. Large data goes to typed rows or `stored_files`.
15. **Progress** is reported through the toolkit event sink (`emit_progress`), never by polling or by writing a workspace file.

## Definition of Ready — every agent

1. It has an `agent.md` that the loader validates, and a `kind`.
2. Every name in its allowed tools exists and is visible to agents. Every delegate is `internal`.
3. Execution agents declare their ports and read upstream data from "Node inputs".
4. `completion_check` / `output_contract` are set where the output feeds other nodes.
5. There is a test run on `ClaudeAgentEngine` against the scripted fake Messages server (`claude_cli` marker).
6. **A `resumable` agent has a resume test:** the run is stopped after a tool call, resumed from its saved session, finishes, and no finished tool call runs twice. It also has a question (`AskUserQuestion`, or its `ask_user` form) suspend/resume test.
7. An agent with `delegates` has a test in which it uses its subagents on fake models, including a resume that brings their transcripts back; an agent whose output feeds other nodes has an `output_format` test.

## Testing (the work, plus an end-to-end run at the first working flow and when every part is complete)

| Suite | Command |
|---|---|
| Runtime (Plan 0) | `uv pip compile Backend/agent_system/requirements.txt --python-version 3.13 --python-platform x86_64-manylinux_2_28`, then `python -m pytest -c Backend/pytest.ini Backend/tests/runtime -m docker` against the built agents image |
| Modeling engine | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/toolpacks/modeling` |
| Deterministic gate | `powershell -NoProfile -ExecutionPolicy Bypass -File Backend/tests/run_tests.ps1 -Suite deterministic` |
| Toolkit / agents / engines | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/toolkit Backend/tests/unit/agentpacks Backend/tests/unit/engines` (the `claude_cli` tests need the bundled CLI, so they run inside the agents image) |
| Tool packs | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/toolpacks Backend/tests/unit/functions` |
| Harness / planning / execution | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/harness Backend/tests/unit/planning Backend/tests/unit/execution` |
| Jobs (unit) | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/jobs` |
| Jobs (integration) | `docker compose --env-file deploy/.env -f deploy/docker-compose.backend.gpu.yml -f deploy/docker-compose.backend.dev.yml up -d postgres application-api compute-worker-cpu`, then `python -m pytest -c Backend/pytest.ini Backend/tests/integration/jobs -m "integration and jobs"` |
| State store (integration) | Start `postgres` + `db-migrate` with the same compose files, then `python -m pytest -c Backend/pytest.ini Backend/tests/integration -m "integration and docker" -k "graph or persistence or transcript or suspend or approval"` |
| Session store conformance | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/execution/test_session_store.py Backend/tests/unit/engines -k "store or resume or ask_user"` |
| Model Gateway / LiteLLM | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/harness/test_model_gateway.py Backend/tests/contracts -k "litellm or locality"` |
| Persistence contracts | `python -m pytest -c Backend/pytest.ini Backend/tests/contracts -k "schema or fencing"` |
| Schema contracts | `python -m pytest -c Backend/pytest.ini Backend/tests/contracts` |
| MONAI / segmentation | `python -m pytest -c Backend/pytest.ini Backend/tests/unit/monai Backend/tests/unit/segmentation` |
| Storage boundary | `powershell -ExecutionPolicy Bypass -File Backend/scripts/check_storage_boundary.ps1` |

**Final check:** `python -c "from agent_system.toolkit.catalog import build; c = build(); c.validate(); print(len(c.tools), len(c.agents))"` prints:

- **64 tools**: 46 moved (the 55 of Plan 2, minus the 7 deleted in Phase 0.2, `write_agent_output` (Phase 10.1) and `scratchpad_write_note` (Phase 12.7); 2 fewer if Phase 3.2 also deletes `scratchpad_write_summary` and `scratchpad_raise_flag`) + `run_segmentation_with_guard` + 6 literature + `ask_user` + `run_modeling` + 9 control tools (`registry_search`, `registry_describe`, `graph_inspect`, `graph_propose`, `execute`, `graph_apply`, `node_cancel`, `node_message`, `artifact_read`). `run_agent`, `plan`, `recover` and `skill_load` are gone: the SDK's subagents and skills replace them;
- **16 agents.**


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
| S10 | `_wait_for_review` retries too few errors; a 5xx is fatal (`module.py:986-1015`). | Removed on the agent path: the review is a `segmentation_review` gate (Phase 15.4). Otherwise `RetryPolicy` + an overall deadline. |
| S11 | The review long-poll is keyed by session, leaks tasks and ignores disconnects (`segmentation_review.py:682-705`). | No agent caller after Phase 15.4. For any remaining caller: key by batch; cancel the waiter; `is_disconnected()`. |
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
| H19 | Progress arrives in bursts. | A sync progress file in the job dir, relayed by `JobClient.wait` (Plan 5). |
| H20 | Writes are refused unless the run is `running` (`repositories/radiomics.py:59-78`). | The supervisor checks before it starts. |

### D. Light tools (Plan 2, done)

All fixed in Plan 2 except L14, which Plan 3 takes:

| ID | Tool | Problem | Fix |
|---|---|---|---|
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

### F. Ingestion (Plan 16, moved to the job platform)

- **G1:** a failed re-run deletes a good series. Fix: delete only what this attempt created.
- **G2:** a long queue looks abandoned. Fix: a job queue + the worker heartbeat (Plan 5).
- **G3:** a restart leaves jobs stuck. Fix: `INTERRUPTED` through the sweep.
- **G4:** prune is never called. Fix: a periodic task on the compute worker.
- **G5:** the webhook token is missing. Fix: send the header.
- **G6:** a blocking 2 GiB write and hash. Fix: chunked, in a thread.
- **G7:** no cancel or list endpoints, and two upload clients. Fix: add the endpoints and keep one client.
- **G8:** the status names differ. Fix: the shared `status` + a `stage` field.

### G. Agent System tasks (Plan 16)

- **K1:** fire-and-forget tasks are not referenced. Fix: `best_effort.schedule_best_effort`.
- **K2:** `stop` sets CANCELLED too early, and `CancelledError` is swallowed. Fix: set the status after the task ends, and re-raise.
- **K3:** `reload=True` is on in production. Fix: dev only.

---

## Appendix P — Persistence Design (revision 3.4)

The database rules every plan follows. The schema itself is built (migrations "o", "p" and "q", Plan 6, Phases 6.1–6.5): **the models and migrations in `Backend/application_api/` are the schema reference**, so this appendix keeps only the rules new code must respect and the changes still to come (migration "t", Phase 6.6; migration "s", Plan 5). Plans cite its decisions (D), invariants (I) and transactions (T). Sections P.3, P.6 and P.7 (the old starting point, the column lists, the final schema) were removed: read the code.

---

### P.1 Decisions

- **D1 — The Application API is the only owner of the database.** Everything else reads and writes through its HTTP API.
- **D2 — A simple `users` table.** Every session has exactly one owner; nothing is shared; no roles, teams or credentials.
- **D3 — One chat per session** (`UNIQUE (session_id)`), created with the session (T0). A new study is a new session (D22).
- **D4 — One live graph per session** (`session_graphs`), with a `version` used for compare-and-swap.
- **D5 — Execution is sequential only.** The order is `graph_nodes.position`; there is no edge table. A DAG later follows §P.5 *No `node_connections` table*.
- **D6 — Nodes are `graph_nodes`:** a target (`target_kind` agent | tool, `target_name`, `target_version`), `params`, `expectations`, a `removed_at` tombstone, and a short per-session handle `node_seq` (the Main sees `n3`). A definition changes only through an applied graph change; `node_id` never changes.
- **D7 — Every graph mutation is a `graph_changes` row**, and that row is the only durable approval of work. Every approval reason is computed **when the change is proposed**, never when a node starts. The proposer mints the `node_id` of every new node. At most one pending change per session. A change that needs approval is applied only by a **user** decision quoting its `content_digest`.
- **D8 — Runs are created lazily, when a node starts** (T4, together with attempt 1). Applying a change creates no run. A node has many runs over time, at most one current.
- **D9 — Attempts are `run_attempts`:** one real try with one submission key, persisted before dispatch. A restart, reconnect or resubmission of an uncertain submission **reuses** the attempt; a new attempt only when execution must truly start again. At most one non-terminal attempt per run always, and per **session** now (a current-phase constraint, §P.4). `unknown` and `suspended` are non-terminal. An attempt is terminal only on evidence that its work stopped; a timeout is a cancel request until the stop is confirmed. A suspended attempt has no running work: the same attempt resumes, and its cancel is confirmed at once.
- **D10 — Segmentation fan-out stays inside one node attempt.** Pair jobs are domain items on `segmentation_pairs`, with `inference_job_id = sha256(batch_correlation_id|pair_id|model|prompt)`; the correlation id is minted once per batch (`segmentation_batches.config`), so every attempt of a run re-attaches to the same jobs and a new run gets new ones.
- **D11 — Results live in the typed domain tables.** Inputs are pinned per port in `resolved_inputs` at first start (D23); the port → result selection is frozen in `node_runs.outputs` at completion. No generic artifact table.
- **D12 — Backends own physical execution state** (the Procrastinate rows and job dir, the MONAI job store). The harness tables keep identity and the last confirmed outcome and copy nothing from the queue; the Application API reads it through the `job_execution_status` view.
- **D13 — `sessions.control_state` is intent only:** `active | paused | archived`. The activity is derived (§P.4) and never stored. Nothing is ever "completed".
- **D14 — Accepting user input is one transaction** (T3): the message and a durable `user_input` event. The Agent System enqueues only after that commit; the inbox cursor `sessions.inbox_handled_through_event_id` advances only after handling (T8).
- **D15 — Session settings are durable:** `execution_mode` (default `auto`), `privacy_mode`, `tokens_used`.
- **D16 — Each run freezes its `execution_policy`** at start (`max_attempts`, `timeout_s`, `idempotent`, `resumable`, `fresh`, `privacy_mode`, `gate_timeout_s`, `seed_from`, `spec_digest`, `engine_version`). Every execution write presents its attempt (D24).
- **D17 — Standalone nnUNet training has its receipt table, `training_jobs`** (T9), outside the session graph.
- **D18 — `render` stored files are never scientific inputs.** Only `retained` files or typed rows can be pinned.
- **D19 — Not persisted:** direct Main tool calls get no run or attempt (their record is the chat, D21); the in-memory inbox queue, resource pools, heartbeats, unsubmitted drafts (`graph_propose` proposals), and start-time refusals for budget or resources (re-evaluated before every start and published as `node_start_refused`). Questions and approvals inside an agent node **are** persisted, as suspended attempts and transcripts (D27), and so is the Main's SDK session (D21).
- **D20 — One vocabulary** (§P.2). Old names live only in migrations, old durable events and the problem catalog.
- **D21 — The Main keeps a saved SDK session** (revised 2026-10-20). Its reasoning, tool calls and results are its SDK transcript in `main_transcript_entries` (one per CUBE.AI session, the Composer and Recovery subagents under their `subpath`), written through `HttpSessionStore` and compacted by the SDK. The chat (`chat_messages`) keeps what the user sees: the user's messages and the Main's replies. Anything that writes research results, waits on a person or runs long is submitted with `execute` as **one graph node**. A control tool's change is idempotent by `sha256(session_id‖tool_use_id)`.
- **D22 — One session = one research line.** For every request the Main decides, and records in the change's ops, whether it is: a question (a direct read tool), a modification of existing work (`UpdateParams` / `RerunNode`, which supersedes that node and every node after it), the next step (`AddNode` at the end), or a different study (it proposes a new session). A change that would supersede a **completed** run always carries the reason `supersedes_completed`, even in Auto mode.
- **D23 — Inputs are resolved strictly, then pinned per port.** Candidates for an input port are the compatible output ports of live nodes earlier in the sequence. One → bound. None → `MISSING_INPUT` (required ports). Several → `AMBIGUOUS_INPUT`, unless `input_sources` names the source (`{port: {node_id, port}}`). Never by distance or type closeness. The Validator checks the whole target sequence on every change. T4 pins, per port, **the current completed run of the selected live upstream node at resolution time**.
- **D24 — Fencing is enforced by the database on every write path.** The shared trigger sits on every table execution writes (every domain table with `created_by_run_id`, `segmentation_pairs`, `stored_files`, `node_runs`, `run_attempts`, `agent_transcript_entries`). A write must declare its path, or it is rejected: **attempt** (`cubeai.attempt_id`), **internal job** (`cubeai.run_id`), **user gate** (`cubeai.actor = 'user'`), **harness** (`cubeai.actor = 'harness'`), **migration** (Alembic only). The rules of each path are in §P.5 *Typed domain tables*. A run-less `stored_files` row (a direct Main result) needs no fence.
- **D25 — Selections freeze with the run.** Selection and approval columns (`model_results.selected`, the pair review and selection columns, `segmentation_batches.review_status`, `cohorts.frozen_at`, `feature_sets.frozen_at`) change only while their run is `running` and current. After completion, `outputs` is the only selection; a different choice is a `RerunNode`.
- **D26 — No dual path.** The AutoGen path does not work against the new schema; its rows are history.
- **D27 — Agent transcripts and suspended attempts.** A `resumable` agent node keeps one Claude Agent SDK session per run (`session_id = run_id`), stored as opaque entries in `agent_transcript_entries` through the Application API (`HttpSessionStore`). Every attempt of the run resumes it; a rerun starts empty, unless its change sets `seed_from = {run_id, message_uuid}` (an earlier run of the same node): T4 checks the seed (it exists, same node, same `privacy_mode`, `spec_digest`, `engine_version`) and the SDK forks the old session up to that message; otherwise the run starts fresh (`seed_from.applied = false`). Only the run's current non-terminal attempt writes; any attempt of the run reads, and so does a seeded run's first attempt. The Application API never parses entries. A transcript is reasoning state, **never a result**. `ask_user`, a tool approval and a pause **suspend** the attempt (T10); the answer is written on the user gate path, and the same attempt resumes. The attempt lists its running calls in `run_attempts.pending_tools`, which T4 rule (f) reads. Transcripts are purged `AGENT_TRANSCRIPT_RETENTION_DAYS` (default 14) after the run is terminal, unless a live `seed_from` names them. The Main's own transcript is `main_transcript_entries` (D21); the Composer and the Recovery Agent are stored there as its subagents.
- **D28 — Approvals come from what the user asks for.** No fixed gate inside a node. A node carries `approval` (`none | before | after`, default `none`), set by the Main from the user's words: `before` → T4 does not start it until a user `ReleaseHold` sets `graph_nodes.start_released_by_change_id` (cleared when the node is edited or rerun); `after` → T6 sets `downstream_hold = 'user_review'` on its completed run, released by a user `ReleaseHold`. Only a change the user authored releases these two. Inside a node the agent asks with `ask_user`. The cohort approval and the segmentation review are domain forms of `ask_user`, used only on request; otherwise the population node freezes its cohort itself (`cohorts.frozen_by = 'auto'`) and the batch is accepted automatically (`review_status = 'auto_accepted'`, pairs `review_decision = 'auto'`).

---

### P.2 Vocabulary

Each word has one meaning in the schema, the code and the API.

| Term | Meaning | Stored as |
|---|---|---|
| session | The owned root scope: one research line, one live graph, one chat | `sessions` |
| session event | A durable, replayable notification in a session | `session_events` |
| session graph | The live graph of a session (header + version) | `session_graphs` |
| graph change | An immutable mutation of the graph, including its approval | `graph_changes` |
| graph node | A stable unit of work with a target and a position; `node_seq` is its handle (`n3`) | `graph_nodes` |
| run | One execution of a node's accepted definition, created when the node starts. **Only this** | `node_runs` |
| attempt | One execution try of a run | `run_attempts` |
| suspension | An attempt waiting with no running work: `ask_user`, a tool approval, or a pause | `run_attempts.state = 'suspended'`, `.suspension` |
| agent transcript | The SDK session of an agent-node run; never a result | `agent_transcript_entries` |
| node approval | The user's wish to approve a node before it starts or after it completes | `graph_nodes.approval`, `.start_released_by_change_id`, `node_runs.downstream_hold = 'user_review'` |
| current run | The run of a node with `superseded_at IS NULL` | `node_runs` |
| input binding | Where an input port of a run comes from (D23), pinned at first start | `graph_nodes.input_sources`, `node_runs.resolved_inputs` |
| output | A run's typed port output | `node_runs.outputs` |
| hold | A completed run that stops the next node: a violated expectation or a user review | `node_runs.downstream_hold`, `hold_released_by_change_id` |
| stored file | File bytes (`retained`, immutable) or a regenerable preview (`render`) | `stored_files` |
| job | A backend execution identity | `run_attempts.submission_key`, `segmentation_pairs.inference_job_id`, `training_jobs` |
| workflow | **Only** an nnUNet training task; work in a session is a session graph | `workflow`, `workflow_details` |
| plan | **Only** Plan mode and the Composer; never a table | — |

**Conventions:** `status` is an entity outcome and `state` an attempt's observed state; control intent is `control_state`. Every JSONB column with a contract carries a version key `v`. Model, schema, repository and route files share the table name. Names follow `db/base.py` `NAMING_CONVENTION`.

---

### P.4 Invariants

- **I1** Every session has one owner; everything it holds inherits it through `session_id`. Every session-scoped route checks ownership.
- **I2** One live graph per session, versioned by `session_graphs.version` (CAS).
- **I3** Execution order is the live sequence by `position`: no branches, parallel nodes or joins.
- **I4** A `node_id` never changes; removal tombstones it; only new work gets a new id, minted in the proposal.
- **I5** Nodes exist only because an applied change created them; a run exists only because the Executor started a live node, and carries the change that accepted it. A pending proposal changes nothing.
- **I6** A node has at most one current run. It is **ready** when the previous live node's current run is completed with no active hold, an `approval: before` node is released, the session is `active`, and no attempt in the session is non-terminal.
- **I7** A run's accepted specification is immutable: its definition in the admitting change, its frozen policy, its `target_version`, its `resolved_inputs` and prompt.
- **I8** Technical restart → new **attempt** of the same run. Intentional rerun or invalidation → new **run**. Reconnect, reconcile, resubmitting an uncertain submission, or resuming a suspension → **same** attempt.
- **I9** At most one non-terminal attempt per run (per session now); an attempt is terminal only once its work is observed stopped.
- **I10** Cancellation intent is durable; `cancelled` is written only from evidence.
- **I11** Only the current attempt of a current run completes it, writes results or selects outputs. Late results of superseded work stay history.
- **I12** Downstream work consumes the upstream results pinned at its start, and the pin never changes.
- **I13** A change that needs approval is applied only on a user decision quoting its digest, and only if still valid; the database adds the reasons it can derive (`PLAN_MODE`, `supersedes_completed`).
- **I14** Input and important notifications are durable before the Main is woken; one monotonic cursor per session tracks handling.
- **I15** Finishing the graph makes the session idle, never "finished".
- **I16** A state change and its event commit together (`publication()`). Provenance is never hard-deleted (transcripts are the one exception, D27).
- **I17** After its run completes, a result's selection never changes.
- **I18** Every write to a fenced table declares its path (D24).
- **I19** An agent-node transcript is written only by its run's current non-terminal attempt, read only within its run (or by a seeded run's first attempt), never parsed by the Application API, and never a result. The Main's transcript is written only by its session's runtime and is never a result either.
- **I20** A suspended attempt holds no running work; it resumes as the same attempt only when the session is `active`, the run is current, and the answer (for a question or a tool approval) is recorded.
- **I21** A user-requested approval (`approval: before`, a `user_review` hold) is released only by a change the user authored.

#### Current-phase constraints (sequential execution only)

They hold because execution is sequential; each has a replacement for a DAG:
- at most one non-terminal attempt **per session** (a suspended attempt counts, so a node waiting on the user blocks the next one) → per run only, with resource pools bounding parallelism;
- ready when the **previous** live node's run completed → when every upstream node (by edges) has a completed run with no hold;
- input candidates are **earlier** nodes → nodes reachable backwards through edges;
- the affected set is the **suffix** after the first changed node → the nodes reachable from the changed ones;
- an expectation hold stops the **next** node → every direct dependant.

#### Derived activity

Never stored; published as `session_state`, checked in this order: `blocked` (an active `expectation_violated` hold, a failed current run with no automatic retry left, or an `unknown` attempt); `pausing` (paused, with an attempt `preparing`, `submitted` or `running`); `awaiting_user` (a `suspended` attempt on `ask_user` or a tool approval, an active `user_review` hold, or an otherwise ready `approval: before` node not yet released); `running` (an attempt `preparing`, `submitted` or `running`); `awaiting_approval` (a pending change); otherwise `idle`. A pause-suspended attempt shows `idle` under `control_state = 'paused'`.

---

### P.5 Rules by table

Only the rules that new code must follow; the columns are in the models.

#### `chat_messages` and the Main's transcript (D21)

- T3 inserts the message and its `user_input` event in one transaction; a replayed `message_id` with other content is a 409.
- The chat holds what the user sees: the user's messages and the Main's replies (with an optional `ui` view hint). The Main's tool calls, tool results and compaction live in its SDK transcript (`main_transcript_entries`), not in the chat.
- **Idempotency:** a change the Main submits has `idempotency_key = sha256(session_id‖tool_use_id)`; a call cut off by a restart is reported to the model by the SDK, and the next turn's graph summary shows whether the change was applied.
- **Sizes:** a tool result to the model is small (refs, ids, summaries); large results go to `retained` `stored_files` with no run, referenced by `file_id`.
- **Asking vs saving:** a question is a direct read tool; saving research results (a cohort) is always a node. A direct call creates no run and is never retried; a Main direct call to a `writes` tool is promoted to an `AddNode` change **before** any effect.

#### `session_events`

- New durable types are registered in `services/event_registry.py`: `user_input`, `graph_change_proposed/applied/rejected/stale/expired`, `node_run_superseded`, `run_attempt_started/ended`, `run_attempt_suspended/resumed`, `gate_resolved`, `node_status`, `expectation_violated`, `node_start_refused`, `graph_idle`, `session_state`, `approval_requested`.
- **Waking** is defined per type in code: `user_input`, failed `node_status`, `expectation_violated`, `graph_idle`, `approval_requested`, and decided or stale changes wake the Main; `gate_resolved` wakes the Executor only.
- Payloads carry `node_id`, `run_id`, `attempt_id` and `change_id` where known. The Agent System periodically re-reads waking events above the inbox cursor.

#### `graph_changes`

- **Approval reasons, all at proposal time:** `PLAN_MODE` (Plan mode, and the change adds, modifies, reruns or releases work; removals of never-started nodes and cancels need none) and `supersedes_completed` are **derived by the Application API itself**; `destructive`, `generated_code`, `untrusted_tool` and `approval_required:<code>` come from the target manifests, sent by the Agent System guardrails. A node's own `approval` (D28) is part of its definition, not a reason.
- **Content** is the full target sequence with `v`: every live node's `node_id` and definition, `rerun_node_ids`, `release_hold_node_ids`, `on_running`, an optional `origin` (`composer` | `recovery`). `content_digest` = SHA-256 of the RFC 8785 canonical JSON, computed by the Application API.
- **One pending change per session:** a new proposal withdraws the old one (or expires it past `APPROVAL_TTL_H`). A change with no reasons skips T1 and is applied directly by T2, which marks any pending change `stale`.

#### `graph_nodes`

- `node_seq` is assigned by T2 on insert and never reused.
- `approval` (`none | before | after`) is part of the definition; T2 clears `start_released_by_change_id` whenever the node is edited or rerun.
- There is no other hold on a node: an expectation or user-review hold lives on the run that set it, so inserting or moving nodes never moves it.

#### No `node_connections` table

Sequential execution (D5): `position` plus D23 express every dependency. **A DAG later:** add `node_connections` and backfill one edge per resolved input port; edges replace the D23 candidate rule (`input_sources` already is an edge); the affected set becomes "reachable from the changed nodes"; drop the session-wide active-attempt index; readiness becomes "every upstream run completed with no hold".

#### `node_runs`

- Inserted only by T4, with attempt 1. `status` is a projection of the attempts, written in the same transaction.
- **`resolved_inputs`** (frozen at first start): `{v:1, ports:{<port>:{node_id, run_id, port, type, ref}}, target_version, inference_model_id?, weights_revision?}`; T4 checks every pin under the session lock (D23).
- **`outputs`** (written with `completed`, immutable): `{v:1, ports:{<port>:{type, ref:{table, ids}, summary, phi}}}`. T6 checks every ref against an allow-list: **produced** tables (cohorts, segmentation batches, feature sets, model results, reports, `retained` stored files) must have `created_by_run_id = run_id`; **reference** tables (`volume_series`, `studies`) must exist; **self** (`node_runs`, for a `ResultSummary`) is this run.
- **Holds:** T6 evaluates the node's `expectations` against `outputs[*].summary`; a violation sets `downstream_hold = 'expectation_violated'` on this run, otherwise an `approval: after` node gets `user_review`. A `ReleaseHold` sets `hold_released_by_change_id` (a `user_review` hold only by the user's change); superseding the run releases it too.

#### `run_attempts`

| Node | Attempt | Backend identity |
|---|---|---|
| Agent node | In-process (`dispatch_kind='in_process'`), driving the run's SDK session; may be `suspended` with no task | None external; internal jobs are keyed through the run (D10; radiomics through `run_id`) and fenced by run |
| Tool node, `execution=job` | One Procrastinate job, sent by the Application API | `submission_key = {sha256(run_id\|tool\|args)}.{attempt_number}`: also the job's `queueing_lock` and the name of its job dir |
| Tool node, `execution=inline` | Inline in the Executor (`dispatch_kind='inline'`) | None |

- **Lifecycle:** `preparing → submitted → running → succeeded | failed | cancelled | interrupted`, plus `unknown`; `preparing → running` is allowed. `running ⇄ suspended` for in-process agent attempts only (T10); `suspended → cancelled` (or `failed` with `gate_timeout`) is confirmed in one step by T7.
- **`suspension`** (`v:2`): `{kind: ask_user | tool_approval | pause, tool_use_id?, form?, payload, since, resume?}`; `resumed_at` is set on each resume.
- **`pending_tools`** (`{v, calls:[{tool_use_id, tool, idempotent, started_at}]}`): the calls the attempt is running, written before a call runs and when it ends.
- **`dispatched_at`** is set when the attempt leaves `preparing`; after an interruption it says whether the work may have had effects.
- A timeout sets `cancel_requested_at` and `error.code='timeout'` while the state stays non-terminal; the attempt becomes `failed` once the stop is confirmed.
- **`usage`** (`{v, input_tokens, output_tokens, model}`) is written when the attempt ends and added to `sessions.tokens_used` in the same transaction.

#### `agent_transcript_entries` (new, migration "t")

- Key `(run_id, subpath, seq)`: `run_id` is the SDK's `session_id`; `subpath` is `''` for the main transcript and the SDK's opaque subpath for a subagent; `seq` is assigned on insert and gives the load order.
- `entry_uuid` is unique per `(run_id, subpath)`, so a re-delivered batch is a no-op. `entry` (JSONB) is stored and returned unchanged. `project_key` is checked against the deployment's one fixed key and not stored.
- **Writes:** the attempt path, by the run's latest attempt in `running` or `suspended`. **Reads:** any attempt of the run; the first attempt of a run whose frozen `seed_from` names it; the Agent System's step timeline. **Purge:** the harness path, for runs terminal longer than the retention and named by no live `seed_from`.

#### `main_transcript_entries` (new, migration "t")

- Key `(session_id, subpath, seq)`: the Main's SDK `session_id` is the CUBE.AI session id; the Composer and the Recovery Agent are subpaths. `entry_uuid` is unique per `(session_id, subpath)`; entries are opaque.
- Written and read only by the Agent System for its own session (owner-checked routes); not an execution table, so no fencing path. Purged with the retention after the session is archived.

#### Typed domain tables

- **Producer allow-lists** (by the node's target name): `cohorts` (`study_population_planner_agent`), `segmentation_batches` (`segmentation_agent`, `run_segmentation_with_guard`), `reports` (`latex_reporter_agent`), `feature_sets` and `radiomics_features` (`feature_extraction_agent`, `radiomics_extract_features`), `model_results` (`run_modeling`, the modeling job tools, legacy `modeling_agent`). `stored_files` has none (any run may store a file), `segmentation_pairs` none (owned through its batch). A new producer joins its list with its own migration; a contract test checks every name against the catalog.
- **The fencing paths (D24):**

| Path (setting) | Who | Allowed writes | Check |
|---|---|---|---|
| attempt (`cubeai.attempt_id`) | The node's own attempt: agent tools, job workers, the Node Runner | Result rows; selection/approval columns; pair bookkeeping; `stored_files`; `node_runs.scratchpad_*` | The attempt row `FOR SHARE`; it belongs to the row's run and is `submitted` or `running` |
| attempt, transcripts and `pending_tools` | `HttpSessionStore` and the engine hooks | `agent_transcript_entries` of the attempt's run; its own `pending_tools` | The run's latest attempt, `running` or `suspended` |
| internal job (`cubeai.run_id`) | Jobs that outlive one attempt (radiomics, segmentation pairs) | Run-scoped natural-key inserts and pair bookkeeping | The run is not completed |
| user gate (`cubeai.actor = 'user'`) | The owner's answer to a suspended attempt, through an ownership-checked route | `cohorts.frozen_at` / `frozen_by`, the pair review/selection columns, `segmentation_batches.review_status`, `run_attempts.suspension.resume` | The run `running` and current; the attempt `suspended` on that question |
| harness (`cubeai.actor = 'harness'`) | T2, T5, T6 (run projection), T7, T10 suspend/resume, the transcript purge | Lifecycle columns of `node_runs` / `run_attempts` only; deleting purgeable transcripts | Under the session lock |
| migration (`cubeai.actor = 'migration'`) | Alembic only | Backfills | — |

- Selection and approval columns also require the run `running` and current (D25). The cohort is frozen **before** its run completes: by the user's approval when asked for, otherwise by the population node itself (`frozen_by = 'auto'`). A batch, feature set or model result that names an unfrozen cohort is refused.
- Execution bookkeeping (pair `status`, `execution_checkpoint`) is allowed for any run that is not completed, a superseded one still stopping included.
- **Fresh reruns:** a run of a node the accepting change reran (`RerunNode`) has `execution_policy.fresh = true`; radiomics must then not reuse rows produced by other runs.
- `segmentation_pairs.inference_job_id` is never re-pointed. A failed pair can be re-executed within the same run only after MONAI supports re-queueing a terminal job under the same id (Plan 15, J1).

---

### P.8 Transaction boundaries

Lock order: **session → graph → run → attempt → domain row**. Each transaction runs inside `publication()`, so its durable event commits with it. **No transaction stays open across an HTTP call to MONAI or a wait on a job.**

| # | Transaction | Atomic effects |
|---|---|---|
| **T0 Create session** | Owner known | Insert the session, its graph (version 0) and its chat; store the opening `prompt` as the first message with its `user_input` event |
| **T1 Propose** | The change has approval reasons | Lock the session (refuse if `archived`); check new `node_id`s are unused; compute the digest; add the derivable reasons; withdraw (or expire) the pending change, then insert this one `pending` (a replayed idempotency key returns the row when the digests match, else 409); publish `graph_change_proposed` + `approval_requested`. Nothing executable changes |
| **T2 Decide + apply** | Auto with no reasons, or a user decision quoting the digest | 1. Lock session → graph; refuse if `archived`. 2. Check ownership and `base_version = version`, else `stale` (past the TTL, `expired`). 3. Validate stable ids, one contiguous sequence, and every `input_sources` entry; if a derivable reason is now missing, the change goes `stale` and is re-proposed. 4. Insert, update in place, tombstone, reorder (assign `node_seq`). 5. **Affected set** = changed, moved, rerun or removed nodes, plus every live node after the first of them. 6. Supersede their current runs: `running` → `superseded_at` + `cancel_requested_at` on the active attempt; terminal → `superseded_at`. 7. Set `accepted_by_change_id` on added, edited and rerun nodes and clear their `start_released_by_change_id`; for `release_hold_node_ids`, set `hold_released_by_change_id` on the current run, or `start_released_by_change_id` on an unstarted `approval: before` node; releasing a `user_review` hold or a start needs a user-authored change (`USER_RELEASE_REQUIRED`). **No runs are created.** 8. `version += 1`; the change is `applied`; any other pending change goes `stale`. 9. Publish |
| **T3 Accept input** | A user message, through the Agent System | Insert the message (a replay compares content; 409 if different), publish `user_input`; refuse if `archived`. The Agent System enqueues only after this commit |
| **T4 Start node / open attempt** | The Executor, before dispatch | 1. Lock session → node (→ run). 2. Require: node live; session `active`; the previous live node's current run `completed` with no active hold; an `approval: before` node released (else `NEEDS_RELEASE`); **no non-terminal attempt in the session**; the node's `accepted_by_change_id` equal to what the Executor read. 3. **First start:** check every `resolved_inputs` pin (D23); insert the run `running` with `accepted_by_change_id`, the frozen `execution_policy`, `target_version`, `resolved_inputs` and the agent prompt, **plus attempt 1**. 4. **Retry**, only when all hold: (a) step 2 passes; (b) the current run is `failed`, or `cancelled` and the user explicitly retries; (c) no attempt of the run is `preparing`, `submitted`, `running` or `unknown`; (d) the latest attempt is `failed` or `interrupted` (or `cancelled` under (b)); a `cancelled` attempt is never retried automatically; (e) `attempt_number < max_attempts`; (f) an `interrupted` attempt with `dispatched_at` set and `idempotent = false` is not retried automatically: the Recovery Agent or the user decides. **For a `resumable` run**, "idempotent" is read from the latest attempt's `pending_tools`: automatic unless it lists a tool with `idempotent = false`. A latest attempt that failed with `transcript_write_failed` is never retried automatically. Then insert attempt `latest + 1`; for a resumable run it resumes the saved session. 5. **Seeded first start:** check the seed (transcript present, same node, same `privacy_mode`, `spec_digest`, `engine_version`) and freeze `seed_from.applied = true`, else start fresh with `applied = false` and the reason. 6. Attempts start `preparing` with `submission_key` and `request`; publish. **Dispatch after commit:** in-process / inline → commit `running` + `dispatched_at`, then spawn; job → the Application API defers the Procrastinate job (`queueing_lock = submission_key`), then commits `submitted` + `dispatched_at`; the worker moves it to `running` before it writes anything |
| **T5 Reconcile** | Restart or lost acknowledgement | `suspended` attempts are left unchanged. In-process and inline attempts → `interrupted`. Job attempts by `submission_key`: running or queued → re-attach; terminal → record the outcome; not found and `preparing` → resubmit the same key and request; not found and `submitted` / `running` → `interrupted` with `record_lost`; an unreachable external backend (MONAI) → `unknown`, which blocks T4 for the session. Each terminal state is projected onto the run (`interrupted` → run `failed`, so T4 can retry); usage is added. **No attempt is created here** |
| **T6 Complete / fail** | The attempt presents `attempt_id` | Require the attempt current, non-terminal and not `suspended`. **Success:** validate `outputs`; attempt `succeeded`, run `completed` + `outputs`; for a current run evaluate the expectations in the same transaction (`expectation_violated` hold and event; otherwise `user_review` for an `approval: after` node, with `approval_requested`); publish `graph_idle` if no live node follows. **Failure:** attempt `failed` + `error`; run `failed`. Add usage. The job dir is purged only **after** this commit, and the queue row after `JOB_RETENTION_HOURS` |
| **T7 Cancel / timeout / privacy switch** | The user, Stop, T2, the Watchdog, a switch to `phi_local` | **Request:** set `cancel_requested_at` on the active attempt (also `preparing` or `unknown`); for a job, also cancel its Procrastinate job (a queued one never starts, a running one gets an abort); a Watchdog timeout also sets `error.code='timeout'`; a switch to `phi_local` requests cancellation of every non-terminal attempt frozen as `standard`, in the same transaction. A node that never started is stopped by a `RemoveNode` change. **Confirm** only once the work is observed stopped: `failed` for `timeout` / `gate_timeout`, otherwise `cancelled`; then the run, unless superseded. **A `suspended` attempt is confirmed in the same transaction as the request** |
| **T8 Inbox advance** | The Main, after handling | Advance `inbox_handled_through_event_id` only when every waking event up to it is handled, after the reply and changes commit; add the turn's token usage |
| **T9 Training** | The training route | Commit the receipt + `workflow.frozen_at`; POST outside any transaction; commit `submitted_at`. The reconciler resubmits the same id on a 404; unfreeze only when no active receipt remains |
| **T10 Suspend / answer / resume** | The Node Runner (suspend, resume), the user (answer) | **Suspend** (harness path): the attempt `running` and current → `suspended` + `suspension` (`v:2`); publish `run_attempt_suspended` (and `node_status` with `waiting_for_user` for a question or a tool approval). **Answer** (user gate path, with the domain form columns when it is a form): the run `running` and current, the attempt `suspended` on that `tool_use_id` with no `resume` yet → set `suspension.resume = {answer \| decision, comment, decided_at}`; publish `gate_resolved` (wakes the Executor, not the Main). **Resume** (harness path): the session `active`, the run current, and for a question or a tool approval `resume` set → `running` + `resumed_at`; publish `run_attempt_resumed`. The **same** attempt (I8) |

---

### P.9 Migrations still to come

#### Cut-over 3: migration "t" (Phase 6.6)

Phase 6.6 lists its steps. Nothing in production wrote the checkpoint tables or a `v:1` suspension, so no data is converted. Two details: the `error.code` list widens with `bad_output`, `transcript_incompatible` and `transcript_write_failed`; `cohorts.frozen_by` is backfilled `user` where `frozen_at` is set. The checkpoint routes answer `410 Gone` until Plan 17 removes them.

#### Execution-adapter prerequisites

1. **MONAI terminal re-queue** (Plan 15, J1): an explicit re-queue of a terminal job under the same id with an incremented internal attempt, releasing the capacity slot on any error. Until it lands, a failed segmentation pair cannot be re-executed within the same run.
2. **Migration "s" (Plan 5):** the Procrastinate schema of the pinned version, the `job_execution_status` view, and the workers' role `cubeai_jobs` (the Procrastinate objects only). `submission_key = {job_id}.{attempt_number}` is the job's `queueing_lock` and names its job dir. A job dir is purged only after T6 and a finished job row only after `JOB_RETENTION_HOURS`, so "not found" means "never sent" for `preparing` and `record_lost` for anything later.

When AutoGen is deleted (Phase 17.1): drop `sessions.intent` and `session_graphs.approved_at`.

---

### P.10 Deliberately not stored now

Do not add these without the trigger in brackets:
- `node_connections` or edge JSON (true DAG execution, §P.5);
- `run_artifacts` or generic artifact metadata (untyped files flowing between nodes);
- a node-level `hold_reason` (a start-time condition that needs a human decision and cannot be known at proposal time);
- saved state for tool nodes (the first resumable tool node that is not a job; until then a tool that works in parts records each part on domain rows);
- a generic `approvals` table (never: approvals of work are `graph_changes` and node approvals; approvals inside a node are suspensions);
- cross-session agent memory (a new Application API table read through a tool, with a PHI guardrail);
- resource grants or pool tables, a controller lease table (durable reservations, or more than one controller);
- fingerprints or a reuse link (cross-version caching);
- a context summary of ours anywhere (never: the SDK compacts the Main's session);
- many chats per session (parallel conversations: drop `UNIQUE (session_id)` on `chats`);
- a `main_turns` table (a turn must resume instead of restart, per-turn metrics are needed, or more than one Agent System worker runs; never runs for turns);
- user roles, teams, sharing, credentials.

---

### P.11 Failure cases and assumptions

| Scenario | Behaviour |
|---|---|
| Crash between T4 and dispatch | In-process: already `running` with `dispatched_at`, so T5 marks it `interrupted` and rule (f) decides. Job: T5 finds `preparing` and resubmits the same key only if the queue has no job for it |
| Timeout while the process is still alive | The attempt stays non-terminal with `cancel_requested_at`; no new attempt starts until the stop is confirmed |
| Write from an old attempt still alive, or racing T6/T7 | Rejected by the trigger (not the active attempt); the `FOR SHARE` lock makes the terminal transition wait for the write, or the write fail |
| Two earlier nodes produce a compatible output | `AMBIGUOUS_INPUT`; the Main or the user names the source in `input_sources` |
| A change makes a later node's input ambiguous | Refused until that node's `input_sources` is set in the same change |
| Superseded run still stopping when the next node is ready | The session-wide index blocks T4 until the old attempt is confirmed stopped |
| Node inserted between a violating node and the next | The hold is on the violating run, so the inserted node is not ready either |
| Restart in the middle of a Main turn | The Main's session is resumed from its store; the SDK reports the cut-off call to the model; the input is handled again because the cursor did not advance |
| Main crashes after applying a change, before T8 | The next turn's graph summary shows the applied change; a literal replay of the call is deduped by the idempotency key |
| The caller omits a derivable approval reason | T1/T2 add `PLAN_MODE` and `supersedes_completed` themselves; T2 marks the change `stale` if they would be missing |
| Session switched to `phi_local` while an external-model run is active | T7 cancels every attempt frozen as `standard`, in the same transaction |
| Late success from a superseded run | Recorded as `completed` + `superseded_at`; never selected or pinned |
| Two proposals at once (the Main and a user edit) | Serialized by the session lock; the second withdraws the first |
| Graph edited while the Executor is about to start a node | T4 re-checks readiness and `accepted_by_change_id`, so a stale definition never runs |
| Crash while an agent node's tool call runs | T5 marks it `interrupted`; the retry resumes the session; finished calls are in the transcript, the cut-off call is reported to the model, and rule (f) blocks the automatic retry when `pending_tools` names a non-idempotent call |
| Restart while a node waits for the user or on a pause | Nothing to do: the attempt is `suspended`; it resumes when the answer arrives or the pause ends (also when the user answered while the Agent System was down) |
| A superseded attempt keeps writing its transcript | Refused; `HttpSessionStore` raises `StaleAttempt`, and the engine stops the attempt |
| A transcript batch is dropped | The attempt ends `transcript_write_failed` and is never retried automatically |
| The SDK is upgraded while runs are suspended | A resume with another `engine_version` fails `transcript_incompatible`; the node is rerun |
| A `seed_from` transcript is gone or the agent changed | T4 starts fresh (`seed_from.applied = false`) |
| The user never answers | `awaiting_user`; with `gate_timeout_s` the Watchdog cancels with `gate_timeout`; Stop or a rerun works at any time |
| `ask_user` called together with other tools | The SDK ignores `defer` for parallel calls; the permission callback refuses with "call it alone" |
| The Main tries to release a user hold | `USER_RELEASE_REQUIRED` |
| LiteLLM is down | Starts report `resource_unavailable`; running attempts fail and are retried within their policy |

**Assumptions:**
1. One Agent System worker: in-process and inline attempts become `interrupted` on restart (suspended ones excepted); each SDK session is a CLI subprocess of that worker.
2. The agent transcript holds what the agent saw and said; the domain rows hold what the node produced. A resumed agent re-reads results (a review decision) from the domain rows, never from its own messages.
3. Detached subprocesses of tools die with the Agent System; verify per tool, or make it a job tool.
4. A single default user until authentication exists. **Multi-user must not be enabled before verified identity.**
5. Every write to a fenced table declares its path; the contract test fails if a new table with `created_by_run_id` lacks the trigger.

---

## Not in this roadmap

- **The frontend Surface:** chat `/skills` UI, a live Graph Canvas with diff preview, the Approvals Panel, and Settings for mode, privacy and models. The backend routes and events are ready for it.
- **The evaluation harness** (system §36).
- **Scaling:** embedding-based registry retrieval, domain sub-planners, a Tool Builder, the Result Verifier, tracing and metrics beyond run usage and timings.
- **`spawn_agent` / `wait_agent`**, and cross-session caching.
- **DAG or parallel execution.** The roadmap stays sequential; Appendix P §P.4 lists the current-phase constraints and §P.5 the five steps that open the DAG path without a rewrite.
- **Authentication and multi-user.** One configured default user owns every session until a verified identity exists (Appendix P D2, assumption 5).
- **Deployment and storage:** a sandbox (container or microVM isolation) for third-party or generated code, the outbound MCP edge (Plan 14, removed 2026-10-20) and any real MCP server, and object storage (MinIO/S3); jobs keep the shared `/storage` mount.
- **Scaling the Agent System:** a multi-worker Agent System (it needs a shared inbox and executor ownership, e.g. PostgreSQL `LISTEN/NOTIFY` or Redis). Agent transcripts already live in the Application API (D27), so they need no change for it.
- **Cross-session agent memory** (the SDK's memory files, `CLAUDE.md`) and **Claude Managed Agents** (hosted sessions and sandboxes): not used. Memory would be an Application API table with a PHI guardrail (Appendix P §P.10); Managed Agents would move the loop and the transcripts out of the deployment, which D1 and PHI-Local rule out.
- **LiteLLM's database features** (virtual keys, budgets, the spend UI): not used; LiteLLM runs from its config file only (Plan 9.2).
