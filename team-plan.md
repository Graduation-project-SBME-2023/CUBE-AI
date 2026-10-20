# CUBE.AI roadmap — how we work: two phases, five people

This file says **who** does **what**, and **in which phase** (decided 2026-10-20). What each piece is and how it is built is in [plan.md](plan.md): plan and phase numbers (Plan 9, Phase 10.2), decisions (D24), invariants (I8) and transactions (T4) refer to it.

## The two phases

The remaining work is done in **two phases**. In each phase the five people work in parallel, one area each, and everyone has an **agentic** part (the agents and the harness around them) and a **core** part (a supporting system) of about the same size. Nobody waits for anybody: each person codes against the others' **contracts** and tests with their **fakes** until the real piece lands.

- **Phase 1 — Essentials.** The backend works end to end. A user asks a research question in the chat. The Main Agent puts the steps in the Session Graph, by itself or through the Composer. The Executor runs them one after the other on the Claude Agent SDK through LiteLLM, the report PDF comes out, and the canvas shows it. Anything that only makes this stronger, safer or smarter waits for Phase 2.
- **Phase 2 — Completion.** Everything else in this roadmap: agent memory across restarts, the user's approvals and questions, the job platform, recovery, privacy, the literature review, skills, and the AutoGen removal.

The implementation order in `plan.md` (*Milestones, order and dependencies*) is still the dependency order inside each phase. It does not say who works when.

## Phase 1 — done when

Inside Docker, with the real database, the real SDK through LiteLLM, and MONAI:

1. A user message → the Main → `execute` / `graph_apply`, or `plan` → the Composer → `graph_apply` → a graph of `study_population_planner_agent` → `segmentation_agent` → `feature_extraction_agent` → `run_modeling` → `result_reporter_agent` → `latex_reporter_agent` → the PDF, and the Main tells the user in the chat.
2. A second question in the chat is answered while a node runs: the Main never waits.
3. A change in the middle (new params for a node) reruns that node and the nodes after it, and the nodes before it keep their runs.
4. Pause stops new starts. Stop cancels the running node. A restart in the middle of a node marks its attempt interrupted, and the node runs again.
5. The canvas shows the graph's nodes and their status, the existing result views show the results, and the chat works.
6. The same flow runs once on the Claude alias and once on the local Ollama alias. The second run measures tool use on a local model (Phase 9.2 item 7); a weak result there is recorded, it does not block Phase 1.

## Phase 1 — interim rules

These are the simplifications that hold until Phase 2. Each one names what replaces it.

| Area | Phase 1 | Phase 2 |
|---|---|---|
| Heavy tools | They stay `INTERIM_HEAVY` and run inline in the Agent System process, as today. The 5 modeling tools and `run_modeling` become graph-visible with their ports (there is no modeling agent). A restart loses a heavy tool that was running, and a cancel stops it only at its next check | Jobs on PostgreSQL + Procrastinate (Plan 5, Phases 7.1 and 7.4) |
| Agent memory | No session store: every agent spec runs with `resumable: false`. An interrupted agent node is retried **from the start** (a new attempt with a fresh SDK session) within the policy; otherwise it goes to the inbox | `HttpSessionStore`: a retry resumes the run's saved session (Phases 6.6, 9.5) |
| Asking the user | No `ask_user` and no suspended attempts. The cohort is frozen automatically (`frozen_by = 'auto'`) and the masks are accepted automatically (`auto_accepted`). Every node has `approval: none` | `ask_user`, tool approvals inside a node, and node `approval: before \| after` (Phases 6.6, 8 follow-up, 9.3 item 3, 11.3) |
| Modes | Auto mode only (the default already) | Plan mode (Phase 11.2) |
| Pause | No new start; a running node runs to its end | A running agent node is suspended at its next tool boundary (T10, Phase 10.6) |
| Guardrails | `check()` and every hook point exist. The rules are: schema and Validator errors (`invalid`), visibility (`permission_denied`), `REQUIRES_NODE`, and the session token budget | Resources, mandatory approvals and PHI rules (Phase 11.1, Plan 13) |
| The Main's context | Rebuilt from the chat, with no compaction | `context_summary` compaction and the PHI filter (Phase 12.3 item 2) |
| Privacy | PHI-Local is not enforced, and the Claude alias is an external model. **Phase 1 runs on de-identified or synthetic data only** | PHI-Local (Phase 13.3) |
| Agents | 9 agents: `main_agent` and `composer`; the 5 execution agents of item 1 above; the 2 segmentation internal agents | + `recovery_agent`, `literature_review_agent` and its 5 internal agents = 16 |
| Skills | None. The guidance Phase 1 needs (the modeling advice included) is written into the prompts | `skills/*.md`, `skill_load` and `/skills` (Phases 3.5, 12.6; how skills reach the agents is the open decision of Phase 3.5) |
| Model settings | The profiles come from `config/models.yaml` only. `ClientSettingsPanel` is left as it is and has no effect | `/client-config` picks a LiteLLM alias per profile, and the panel follows (Phase 9.2 item 5) |
| Expectations | The Application API already evaluates them in T6, and the Executor honours the hold; nothing more is built | The `expectation_violated` path to the Main (Phase 10.3) |
| AutoGen | Frozen, not deleted. `/plan`, `/run` and `/session/{id}/resume-pipeline` return `410 Gone` | Deleted (Phases 17.2–17.4) |

## Scope by plan

| Plan | Phase 1 | Phase 2 |
|---|---|---|
| 0 | CI green | – |
| 1, 2 | done | – |
| 3 | 3.1; 3.2 for the 5 Phase 1 execution agents; 3.3 for the segmentation internal agents; 3.4 `main_agent` and `composer` | 3.2 and 3.3 for the literature review; 3.4 `recovery_agent`; 3.5 |
| 5 | – | all |
| 6 | done (6.1–6.5) | 6.6 |
| 7 | 7.3 inline; the 7.1 ports and `graph` visibility (still `INTERIM_HEAVY`); 7.5 `on_result` hooks | 7.1 job manifests, 7.4, 7.5 file sink |
| 8 | done (8.1–8.4) | the 2026-10-19 follow-up |
| 9 | 9.0 (both models), 9.1, 9.2 items 1–4, 6, 7; 9.3 items 1, 2, 4–6; 9.4 without `defer`, saved sessions and seeds; 9.7 with the automatic freeze and acceptance | 9.2 item 5 and the locality filter; 9.3 item 3; 9.4 `defer`, saved sessions, seeds, mirror failures; 9.5; 9.6; the 9.7 review and approval parts |
| 10 | 10.1 without 4a, 9 and `pending_tools`; 10.2 without suspensions and user approvals; 10.4; 10.5 without the suspended and resumable rules; 10.6 with the Phase 1 pause | the rest of 10.1, 10.2, 10.5, 10.6; 10.3 |
| 11 | 11.1 with the Phase 1 rules | the rest of 11.1; 11.2; 11.3 |
| 12 | 12.1; 12.2 without `recover` and `skill_load` (`node_message` to running nodes only); 12.3 without compaction and the PHI filter; 12.4; 12.5; 12.7 without the answer and steps routes | `recover`, `skill_load`, `node_message` to suspended nodes; 12.3 item 2; 12.6; the 12.7 answer and steps routes |
| 13 | – | all |
| 15 | 15.1, 15.2 | 15.3, 15.4 |
| 16 | – | all |
| 17 | 17.1 | 17.2–17.6 |

## Frontend: whoever changes the backend changes the frontend

There is no separate frontend plan for these changes. When a backend change breaks a screen, the same person updates that screen in the same area. In Phase 1 the changes are small, and they sit in four files of `Frontend/OHIF-Viewer/extensions/research-playground/src/`:

- **Unchanged:** the result views (cohort, segmentation, features, modeling, report), because the tools keep sending the same view events from their `on_result` hooks; the chat input (`POST /session/{id}/input`); `node_status`; `/pause` and `/stop`; and the mapping of events to nodes by `source`.
- **`PlaygroundCanvas.tsx` and `ResearchPlayground.tsx` (Person 2):** the canvas builds its nodes from the Session Graph (`GET /session/{id}/graph` and the `graph_change_*` events) instead of `plan.steps`, and the Plan / Run buttons that call `/plan` and `/run` are removed: the Main starts the work.
- **`services/api.ts` (Person 2):** `plan-state` and `resume-pipeline` are replaced by the graph read.
- **`ChatSidebar.tsx` (Person 5):** the "plan received" message is replaced by the Main's replies.

In Phase 2: the cohort approval and segmentation review screens answer `ask_user` (Person 4); a node waiting for the user, and a seeded rerun, show on the canvas (Person 2); the graph-change approval card and `ClientSettingsPanel` follow the approvals and the model settings (Person 1).

## Who does what — Phase 1

| Person | Agentic part | Core part | Frontend | Plans and phases |
|---|---|---|---|---|
| **Person 1 — Agent Runtime & Model Infrastructure** | `ClaudeAgentEngine`: the tool bridge, the options, the PreToolUse / PostToolUse / Stop hooks, streaming, cancel, `open_main()` / `turn()` for the Main, and the one-shot `query()` for the Composer | LiteLLM with a Claude alias and a local Ollama alias, the Model Gateway, the runtime dependencies, CI green; the Guardrails core with the Phase 1 rules | – | 0 (rest), 9.0, 9.1, 9.2, 9.3, 9.4, 11.1 (Phase 1 parts) |
| **Person 2 — Graph & Planning** | The Main's control tools; the Composer (`composer` agent.md, the `plan` tool, validate and repair) | The Application API gaps the flow needs, the graph routes, the event registry | The canvas from the Session Graph | 3.4 item 2, 12.2, 12.5, 12.7 items 1–3 (Phase 1 parts) |
| **Person 3 — Execution** | The Executor and the Node Runner running agent nodes on the engine | Tool nodes inline, the runtime pools, the Watchdog (heartbeat, timeout, interrupted, retry within the policy), pause / resume / stop, restart reconciliation | – | 10.1, 10.2, 10.4, 10.5, 10.6 (Phase 1 parts), 17.1 item 1 (with Person 5) |
| **Person 4 — Domain Agents & Medical Imaging** | The `agent.md` loader; the population, segmentation and feature-extraction agents; the 2 segmentation internal agents; the segmentation module and the cohort labels on the engine | The MONAI fixes segmentation needs, with their first real tests | – | 3.1, 3.2 (3 agents), 3.3 (segmentation part), 9.7 (Phase 1 parts), 15.1, 15.2 |
| **Person 5 — Main Agent & Data/Analysis Tools** | The Main Agent: `main_agent` agent.md, the inbox, the context, the Main loop; the result and LaTeX reporter agents | `run_modeling` and the modeling, radiomics and reporting tools in the flow, with their ports and view hooks | The chat sidebar | 3.2 (2 agents), 3.4 item 1, 7.1 ports, 7.3, 7.5 hooks, 12.1, 12.3, 12.4 (Phase 1 parts), 17.1 items 2–5 (with Person 3) |

The Composer moved to Person 2 for Phase 1 (2026-10-20): `plan` is a control tool, and its validate-and-repair loop runs the Validator, both in Person 2's area.

## Who does what — Phase 2

| Person | Agentic part | Core part | Frontend | Plans and phases |
|---|---|---|---|---|
| **Person 1** | The remaining Guardrails rules, Plan mode, graph-change approvals, and tool approvals inside a node (the engine's `defer`) | The engine's saved sessions, resume and fork options; the Model Gateway user settings and the locality filter hook | The approval card, `ClientSettingsPanel` | 9.2 item 5, 9.4 (rest), 11.1 (rest), 11.2, 11.3 |
| **Person 2** | The Recovery Agent (`recovery_agent` agent.md, `recover`) and seeded reruns; SDK subagents; `node_message` to suspended nodes | Migration "t" and Phase 6.6, `HttpSessionStore`, the Plan 8 follow-up, PHI-Local | Waiting and seeded nodes on the canvas | 3.4 item 3, 6.6, 8 follow-up, 9.5, 9.6, 12.2 (rest), 12.7 (rest), 13.1, 13.3 |
| **Person 3** | Suspend and resume in the Node Runner and the Executor, resumable retries, expectations, resource failures | The job platform (Plan 5), the compute workers, the job lifecycle | – | 5, 10.1–10.6 (rest), 10.3, 13.2 |
| **Person 4** | `ask_user`; the cohort approval and the segmentation review through it; the literature review agent and its 5 subagents | `MonaiHttpBackend`, the segmentation client, MONAI training | The cohort approval and segmentation review screens | 3.2 and 3.3 (literature), 9.3 item 3, 9.7 (rest), 15.3, 15.4, 16.2 |
| **Person 5** | Skills, the Main's context compaction, the Main's approval guidance (D28) in its prompt | The heavy tools as jobs (job manifests, H1–H18), long waits, ingestion, Agent System tasks | – | 3.5, 7.1 (job manifests), 7.4, 7.5 (file sink), 12.3 item 2, 12.6, 16.1, 16.3, 16.4 |
| **All** | Plan 17.2–17.6: each person does the items of their own area | | | 17 |

Each plan in `plan.md` starts with an **Owner** line that names the phase of each part.

## Contracts first

At the start of each phase, each person merges the contracts of that phase: the types and function signatures the others call, each with a fake and its tests. Prompts, logic and real backends can be drafts; the shapes cannot.

**Phase 1 (ready at Sync A):**

| Contract | Owner | Where | Fake the others use |
|---|---|---|---|
| Engine types: `run(..., resume)`, `open_main()` / `turn()`. `AgentStatus.SUSPENDED`, `Suspension` and `Resume` are defined now and used in Phase 2 (Phase 9.3) | Person 1 | `toolkit/agents/engine.py` | `FakeAgentEngine` |
| `guardrails.check(action, ctx) -> Decision` and the action types (Phase 11.1) | Person 1 | `harness/guardrails.py` | `AllowAllGuardrails` |
| `options_for(profile, ctx) -> ModelOptions` (Phase 9.2) | Person 1 | `harness/model_gateway.py` | `FakeModelGateway` |
| `SessionExecutor`: `start`, `stop`, `wake(reason)`, `pause`, `resume` (Phase 10.2) | Person 3 | `execution/executor.py` | `FakeExecutor` (records the calls) |
| `agent.md` format and loader, the 9 Phase 1 specs (draft prompts allowed) (Plan 3) | Person 4 | `toolkit/agents/loader.py`, `agentpacks/` | the real loader with draft prompts |
| Control tools: names, input models, `RunAccepted`; `plan` → `GraphProposal` (Phases 12.2, 12.5) | Person 2 | `toolpacks/control/`, `planning/proposals.py` | handlers backed by `InMemoryGraphStore` |
| Inbox item types (Phase 12.1) | Person 5 | `harness/inbox.py` | – (plain models) |
| The ports and `graph` visibility of `run_modeling` and the modeling tools (Phases 7.1, 7.3) | Person 5 | `toolpacks/modeling/` | the tools themselves against `fake_domain_api` |

**Phase 2 (ready at Sync E):**

| Contract | Owner | Where | Fake the others use |
|---|---|---|---|
| Phase 6.6 shapes: transcript, `pending_tools`, answer, node `approval`, start release, `NEEDS_RELEASE`, `USER_RELEASE_REQUIRED` | Person 2 | `application_api/schemas/`, `clients/harness_client.py`, `execution/graph_store.py` | `InMemoryGraphStore` with the new methods; `fake_domain_api` routes |
| `SeedFrom{run_id, message_uuid}`, `NodeDef.approval` (Plan 8 follow-up) | Person 2 | `planning/graph_model.py`, `application_api/core/graph_content.py` | none needed (pure models) |
| `HttpSessionStore` (Phase 9.5) | Person 2 | `execution/session_store.py` | an in-memory store over `InMemoryGraphStore` |
| The `ask_user` tool (Phase 9.3 item 3) | Person 4 | `toolpacks/interaction/` | the real tool on `FakeAgentEngine` with a scripted suspension |
| `JobClient`, `JobRequest`, `JobRecord`, the job statuses (Phase 5.1) | Person 3 | `jobs_core/` | `jobs_core/backends/in_memory.py` |
| `MonaiHttpBackend` as a `JobClient` (Phase 15.3) | Person 4 | `jobs_core/backends/monai_http.py` | the in-memory `JobClient` |
| Job tool manifests (`run_modeling`, the modeling and radiomics tools, Phase 7.1) | Person 5 | `toolpacks/modeling/`, `toolpacks/radiomics/` | the in-memory `JobClient` |

## Sync points

| Sync | What is true | Who |
|---|---|---|
| **A. Phase 1 contracts** | Every Phase 1 contract is merged with its fake and tests. Person 1's spike (Phase 9.0) has answered, for both aliases | All |
| **B. Real pieces behind the contracts** | The SDK engine runs against the fake Messages server (Person 1); the control tools and the Composer on `InMemoryGraphStore` (Person 2); the Executor on fakes (Person 3); the 9 agents load and run on `FakeAgentEngine` (Person 4); the Main loop on fakes and `run_modeling` inline (Person 5) | Each person alone |
| **C. One flow on fakes** | In one test: user message → Main → `plan` → `graph_apply` → Executor → agent nodes (fake engine) → `run_modeling` (inline) → `graph_idle` → Main reply; plus a change in the middle and a restart. Person 5 writes the test; everyone fixes their part | All |
| **D. Phase 1 done** | "Phase 1 — done when" holds inside Docker, the canvas included | All |
| **E. Phase 2 contracts** | Every Phase 2 contract is merged with its fake and tests | All |
| **F. Real pieces behind the contracts** | Migration "t" and `HttpSessionStore` (Person 2); the job platform's unit suite (Person 3); `ask_user` and the literature subagents on the fake Messages server (Persons 1, 4); approvals and Plan mode (Person 1) | Each person alone |
| **G. The full flow** | The Phase 1 flow plus: an `ask_user` cohort approval that suspends and resumes across a restart, a heavy tool as a Procrastinate job, a node `approval: after`, a recovery proposal, and a `phi_local` session; first on fakes, then inside Docker | All |
| **H. Cut-over** | Plan 17.2–17.6 | All |

## Rules for working in parallel

1. **Code against contracts only.** If what you need is not merged yet, use its fake; never wait, and never import another person's unmerged branch.
2. **A contract change is a small PR** that edits the contract, its fake and the Contracts table above, reviewed by the owner and by the people who use it. Nothing else changes a contract.
3. **One branch per person** off `New-Architecture` (`track/p1-runtime`, `track/p2-persistence`, `track/p3-execution-jobs`, `track/p4-domain-imaging`, `track/p5-main-analysis`). Small PRs into `New-Architecture`, each passing the deterministic gate; rebase on it often. Nobody pushes to `New-Architecture` directly.
4. **Folders have owners** (table below). A change in another person's folder is a small PR reviewed by that person.
5. **Shared files** take small, additive edits only: `agent_system/app.py` (one router or one lifespan line per PR), `services/event_registry.py` (append event types), `Backend/agent_system/requirements.txt` (UTF-16 LE, CRLF; one package per PR), `deploy/docker-compose.backend.gpu.yml` (one service per PR), and `CLAUDE.md` (Plan 17.5).
6. **Migrations (Phase 2):** Person 2 owns migration "t", Person 3 owns migration "s". The one merged second sets its `down_revision` to the head at that time.
7. **`plan.md` and `team-plan.md`:** each person edits only their own plans in `plan.md` (status, as-built notes) and their own rows of the Contracts tables here. A change that crosses areas is a small PR reviewed by the people it touches.
8. **Phases do not mix.** During Phase 1, nobody starts Phase 2 work in a shared file, and no Phase 1 PR depends on a Phase 2 item. A person who finishes their Phase 1 part early helps another Phase 1 area (as a small PR reviewed by its owner), or starts their own Phase 2 contracts.
9. **Done for a PR:** its tests pass with fakes, and it depends on nothing unmerged. A phase of a plan is done when its "Done when" holds for the parts in that phase; mark it in the Milestones table in the same PR.

| Person | Owns |
|---|---|
| **Person 1** | `toolkit/engines/` (except `subagents.py`), `toolkit/agents/engine.py`, `harness/guardrails.py`, `harness/approvals.py`, `harness/model_gateway.py`, `config/models.yaml`, `deploy/litellm/`, the agents image dependencies, the CI workflows; Phase 2: `ClientSettingsPanel.tsx` and the approval card |
| **Person 2** | `application_api/` (the session-graph, run, attempt and transcript files; migration "t"), `planning/graph_model.py`, `planning/proposals.py`, `application_api/core/graph_content.py`, `clients/harness_client.py`, `execution/graph_store.py`, `execution/in_memory_graph_store.py`, `execution/session_store.py`, `toolkit/engines/subagents.py`, `toolkit/delegation/`, `toolpacks/control/`, `api/routes/graph.py`, `agentpacks/core/composer/`, `agentpacks/core/recovery_agent/`, `execution/recovery.py`, `harness/privacy.py`; frontend: `PlaygroundCanvas.tsx`, `ResearchPlayground.tsx`, `services/api.ts` |
| **Person 3** | `jobs_core/` (except `backends/monai_http.py`), `compute_worker/`, `toolkit/runtime/job.py`, the job files of `application_api/` (migration "s", the job routes), `execution/executor.py`, `node_runner.py`, `watchdog.py`, `resources.py`, `expectations.py`, `config/resources.yaml` |
| **Person 4** | `agentpacks/` (except `core/`), `toolkit/agents/loader.py`, `toolpacks/population/`, `toolpacks/segmentation/`, `toolpacks/literature/`, `toolpacks/interaction/`, the segmentation module, `Backend/monailabel/`, `Backend/apps/radiology/` trainers, the training files of `application_api/` (16.2), `jobs_core/backends/monai_http.py`; Phase 2 frontend: `DataCohortView.tsx` (the approval) and `views/segmentation/` (the review) |
| **Person 5** | `agentpacks/core/main_agent/`, `skills/`, `harness/inbox.py`, `main_loop.py`, `session_runtime.py`, `context_manager.py`, `skills_loader.py`, `toolpacks/modeling/`, `toolpacks/radiomics/`, `toolpacks/reporting/`, the ingestion code; frontend: `ChatSidebar.tsx` |

`planning/validator.py`, `planning/inputs.py`, `execution/affected.py` and `execution/diff_engine.py` (Plan 8, built) belong to Person 2; a change there is reviewed by Person 2.
