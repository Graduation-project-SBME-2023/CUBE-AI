# CUBE.AI roadmap — how we work: one phase, five people

This file says **who** builds **what**, and **in which order** (decided 2026-10-21; it replaces the two-phase split of 2026-10-20; Person 2's area revised the same day, after Plan 3 turned out already built on the pre-SDK design, and again later that day: Plans 15 and 16 moved to Person 1, Person 2 took the Plan 2 tool changes of Phase 0.2, and Person 1's plans were made whole and independent: the pieces of them that touched another area moved to that area; then, to even the load, Person 2 took Phase 0.2 from Person 4, Phases 12.5 and 12.6 from Person 5, and Phase 11.3 from Person 3). What each piece is and how it is built is in [plan.md](plan.md): plan and phase numbers (Plan 9, Phase 10.2), decisions (D24), invariants (I8) and transactions (T4) refer to it.

## How the work is split

All the remaining work is done in **one phase**. There is no "first phase" and "second phase": each person owns one area from the start to the end of the roadmap, and works through the plans of that area in the order given below.

- **Five areas, one person each.** Each area is a set of whole plans (or whole phases of a plan) that belong together, so that the person who builds a piece is also the person who builds what uses it most.
- **Simple version first, then the full version, without stopping.** Inside an area, each piece is first built in its simple form (heavy tools inline, no saved sessions, no questions to the user, Auto mode only), so the first working flow comes early. The same person then completes it. Nobody switches to other work in between.
- **Short waits only.** The order inside each area is chosen so that what you need from someone else is ready by the time you reach it. Where a wait cannot be avoided, it is a few days at most, and you work against that person's **fake** in the meantime (see *Contracts first*).
- **One checkpoint, not a phase:** the **first working flow** (a question in the chat → the PDF, inside Docker). It is a date on which everything is tested together; nobody stops at it.

The implementation order in `plan.md` (*Milestones, order and dependencies*) is the dependency order. It does not say who works when; this file does.

## The five areas

| Person | Area | Plans and phases, in order |
|---|---|---|
| **Person 1** | Compute jobs and MONAI (alone: whole plans, no dependency on anyone) | Phase 5.1 → Phases 7.1 (ports), 7.3 → Plan 15 (15.1–15.4) → the rest of Plan 5 → Phases 7.1 (job manifests), 7.4, 7.5 → Plan 16 (16.1–16.4) |
| **Person 2** | Agents, subagents and approvals | Phase 3.0 (rework of the built Plan 3) + Phase 0.2 (remove what the SDK replaces) → Phase 9.6 → Phase 12.5 → Phase 9.7 (with the segmentation review) → `ask_user` (Phase 9.3 item 3) → Phase 12.6 → Phase 11.3 → Phase 13.1 |
| **Person 3** | Execution and session state | Phase 6.6 → Plan 10 → Phase 13.2 |
| **Person 4** | Agent engine and models | Phase 9.0 spike → Phase 0.1 → Phases 9.1–9.4 → Plan 8 follow-up → Phase 9.5 → Phase 13.3 |
| **Person 5** | Main Agent and session layer | Plan 12 (12.1–12.4, 12.7) → Phases 11.1, 11.2 → Phase 17.1 |
| **Everyone** | Cut-over | Plan 17.2–17.6: each person removes and documents the items of their own area |

### Person 1 — Compute jobs and MONAI

**Builds:** the job platform on PostgreSQL + Procrastinate, the heavy tools as jobs, the whole MONAI side (the MONAI fixes, the MONAI job backend, the segmentation client), and the other job systems, MONAI training included.

**Works alone.** Plans 5, 7, 15 and 16 are Person 1's, whole: nobody else owns a phase in them, and none of their phases waits for another person's work. The pieces that touched another area were moved to that area:

- the Node Runner's skip of the run's temp cleanup while jobs are active (H13) → Phase 10.1 (Person 3), on the `JobClient.list` of Phase 5.1;
- the segmentation review (S10: `ask_user`, `auto_accepted`, removing `_wait_for_review`) → Phase 9.7 (Person 2);
- the `SessionRuntime` stop ordering (K2) → Phase 12.4 (Person 5).

Others build on Person 1's work (the `JobClient` contract, the heavy tools' ports, the segmentation client); Person 1 builds on nobody's. The one shared point left is the migration order: migration "s" and Person 3's migration "t", whichever merges second sets its `down_revision` (rule 7).

**Order:**

1. **Phase 5.1 first:** `JobClient`, `JobRequest`, `JobRecord`, the job statuses and the in-memory backend. Persons 2 and 3 use this contract, so it is merged in the first days.
2. **Phase 7.1 ports and `graph` visibility, and Phase 7.3 `run_modeling`**, still `INTERIM_HEAVY` and inline, so that the first working flow can run the modeling step.
3. **Plan 15:** the MONAI safety net (the first real tests of `inference_jobs.py`), the MONAI server fixes (J1, J2, …), `MonaiHttpBackend` on the `JobClient` contract, and the segmentation client on it (J7, J13, S1–S9). Merge 15.4 early: Person 2's Phase 9.7 builds on it.
4. The rest of **Plan 5** (5.2 the Application API queue and migration "s", 5.3 the compute worker, 5.4 toolkit integration, 5.5 deploy and CI).
5. **Phases 7.1 (job manifests), 7.4, 7.5:** the 8 heavy tools become jobs.
6. **Plan 16:** 16.1 long waits, 16.2 MONAI training as one server-side job (T1–T9), 16.3 ingestion as a job, 16.4 Agent System tasks (K1, K3).

**Waits:** none.

### Person 2 — Agents, subagents and approvals

**Builds:** the agents (Plan 3 is already built, on the design that came before the Claude Agent SDK, so it is reworked first), the removal of everything the SDK replaces (Phase 0.2), the SDK subagents (the Composer and the Recovery Agent included), the segmentation module on the new engine, the skills, and everything the user is asked or approves: the `ask_user` forms and the approvals.

**Why this area:** the internal agents (Plan 3) are exactly what the subagents (Phase 9.6) run, and Phase 0.2 removes what those agents used before the SDK (`run_agent`, `agent_as_tool`, the bookkeeping tools), so one person reworks the agents and removes what they stop using. The Composer (Phase 12.5) and the Recovery Agent (Phase 13.1) are subagents, and the `/skills` list (Phase 12.6) reads the skills of Phase 3.5. `ask_user` and the tool approvals inside a node (Phase 11.3) are the same mechanism (a `defer`ed call, a suspended attempt, the user's answer on resume), and the graph-change approvals sit next to them in the same screens.

**Order:**

1. **Phases 3.0 and 0.2, first days:** rework the built Plan 3 for the SDK (the removed, added and changed items are listed in `plan.md`, Phase 3.0), and remove what the SDK and the Node Runner replace: `run_agent`, `agent_as_tool` and the rest of item 1 in `toolkit/`, `RecoveryProposal` (item 2), and the Plan 2 bookkeeping tools (item 3), with their tests (item 4). The `agent.md` format and loader are a contract, so they merge first. Merge Phase 0.2 before Person 4 starts Phase 9.3, which edits the same toolkit contracts.
2. **Phase 9.6:** internal agents become SDK subagents (`AgentDefinition`s built from their specs), their transcripts stored under the parent's `subpath`, the depth and concurrency limits; the literature review's 5 subagents; the Main's `composer` and `recovery_agent` subagents. It plugs into Person 4's `ClaudeAgentEngine`.
3. **Phase 12.5:** the Composer's repair loop on `graph_propose` (Person 5's control tools contract), the Main deciding on its `proposal_id`; `make_agent_tool`, `validate_and_repair` and `PLAN_REPAIR_ROUNDS` removed.
4. **Phase 9.7:** the segmentation module calls the engine instead of AutoGen, the segmentation review (S10: `ask_user` when the user asked for it, otherwise `auto_accepted`; `_wait_for_review` loses its agent caller), and the cohort labels use the Model Gateway. It builds on Person 1's Phase 15.4 (the segmentation client on `MonaiHttpBackend`), which is merged long before.
5. **`ask_user`** (Phase 9.3 item 3, `toolpacks/interaction/`): the cohort approval and the segmentation review through it, and their screens in the frontend.
6. **Phase 12.6:** the `/skills` command (`harness/skills_index.py`), on Person 5's command handling.
7. **Phase 11.3:** approvals (pending graph changes, the decision route and chat commands, tool approvals inside a node that suspend the attempt), and the approval card in the frontend. It runs on Person 3's Phase 6.6 and T10 and Person 4's `defer` hook.
8. **Phase 13.1:** the Recovery Agent as the Main's SDK subagent.

**Waits:** Phase 9.6 needs Person 4's `ClaudeAgentEngine` (Phase 9.4), and Phase 9.7 needs Person 1's Phase 15.4. Phases 3.0 and 0.2 come first and need nothing from anyone. By the time Person 2 reaches 9.6, the engine is in; by 9.7, the segmentation client; by 11.3, Person 3's Phase 6.6 and Plan 10.

### Person 3 — Execution and session state

**Builds:** the database side of agent memory and user approvals, and everything that runs the graph.

**Order:**

1. **Phase 6.6:** migration "t", agent transcripts, `pending_tools`, the answer route, node `approval`, the start release, `NEEDS_RELEASE`, `USER_RELEASE_REQUIRED`. Merge its shapes first (in `InMemoryGraphStore` and `fake_domain_api`), because Persons 4 and 5 use them.
2. **Plan 10:** the Node Runner (including H13: no temp cleanup while the run has active jobs), the Executor loop, expectations, live changes, the Watchdog, control and restart. Simple version first: no suspensions, a pause that only stops new starts, retries from the start. Then suspend and resume, resumable retries and the full pause.
3. **Phase 13.2:** resource failure handling.

**Waits:** about 2–3 days for the answer of Person 4's spike (Phase 9.0), which says how `defer` and saved sessions behave. Phase 6.6 fills that time. The Executor then runs on `FakeAgentEngine`.

### Person 4 — Agent engine and models

**Builds:** the Claude Agent SDK engine, LiteLLM and the Model Gateway, the runtime (Phase 0.1), the Plan 8 follow-up, the saved sessions, and PHI-Local.

**Order:**

1. **Phase 9.0 spike, on day 1.** Persons 2, 3 and 5 are waiting for its answer, for both the Claude alias and the local Ollama alias.
2. **Phase 0.1:** the rest of it (CI green), together with the runtime dependencies of 9.1. Phase 0.2 is Person 2's; start Phase 9.3 once it is merged.
3. **Phases 9.1–9.4:** the dependencies, LiteLLM and the Model Gateway (`options_for`), the toolkit contracts for the engine (`run(..., resume)`, `SUSPENDED`, `open_main()` / `turn()`), and `ClaudeAgentEngine`. The engine types and `FakeModelGateway` are contracts: merge them early.
4. **Plan 8 follow-up:** `seed_from` and `NodeDef.approval` in the graph model, the Validator and the Diff Engine (`apply(…, session_id, tool_use_id)`).
5. **Phase 9.5:** `HttpSessionStore` on Person 3's Phase 6.6 routes.
6. **Phase 13.3:** PHI-Local (the Model Gateway's locality filter and the PostToolUse filter).

**Waits:** none. Phase 9.5 needs Person 3's Phase 6.6, which is merged long before Person 4 reaches it.

**Subagents (Phase 9.6) are Person 2's**, built on this engine: Person 4 keeps the engine's options and hooks, and reviews Person 2's subagent code.

### Person 5 — Main Agent and session layer

**Builds:** everything between the user and the Executor: the inbox, the Main loop, the control tools, the routes and events, the Guardrails and the modes, the API cut-over, and the canvas and chat. The Composer (Phase 12.5) and the `/skills` command (Phase 12.6) are Person 2's.

**Order:**

1. **Phases 12.1, 12.2:** the inbox item types and the control tools (`execute`, `graph_apply`, `graph_propose`, `graph_inspect`, …), on `InMemoryGraphStore`. Their names, input models and `RunAccepted` are a contract: merge them first.
2. **Phase 11.1 core:** `guardrails.check(action, ctx) -> Decision` and the hook points, with `AllowAllGuardrails` as the fake the others use.
3. **Phases 12.3, 12.4:** the Main's context, the Main loop (with K2, the `SessionRuntime` stop ordering).
4. **Phase 12.7:** the Agent System routes and events; the canvas built from the Session Graph and the chat sidebar.
5. **The rest of 11.1, and 11.2:** the full Guardrails rules and Plan mode.
6. **Phase 17.1:** the API cut-over, with Person 3.

**Waits:** the Main loop (12.4) needs `open_main()` / `turn()` from Person 4, a few days after the start. Phases 12.1 and 12.2 fill that time.

## The first working flow (the checkpoint)

Inside Docker, with the real database, the real SDK through LiteLLM, and MONAI:

1. A user message → the Main → `execute` / `graph_apply`, or the Composer → `graph_apply` → a graph of `study_population_planner_agent` → `segmentation_agent` → `feature_extraction_agent` → `run_modeling` → `result_reporter_agent` → `latex_reporter_agent` → the PDF, and the Main tells the user in the chat.
2. A second question in the chat is answered while a node runs: the Main never waits.
3. A change in the middle (new params for a node) reruns that node and the nodes after it; the nodes before it keep their runs.
4. Pause stops new starts. Stop cancels the running node. A restart in the middle of a node marks its attempt interrupted, and the node runs again.
5. The canvas shows the graph's nodes and their status, the result views show the results, and the chat works.
6. The same flow runs once on the Claude alias and once on the local Ollama alias. A weak result on the local model is recorded; it does not block the checkpoint.

**Until a piece's full version lands, the flow uses its simple version:**

| Area | Simple version | Full version | Who |
|---|---|---|---|
| Heavy tools | `INTERIM_HEAVY`, inline in the Agent System process | Procrastinate jobs (Plan 5, Phases 7.1, 7.4) | Person 1 |
| Agent memory | `resumable: false`; an interrupted node is retried from the start | `HttpSessionStore`; a retry resumes the saved session (Phases 6.6, 9.5) | Persons 3, 4 |
| Asking the user | No `ask_user`; the cohort is frozen and the masks accepted automatically; every node `approval: none` | `ask_user`, tool approvals, node `approval: before \| after` (Phases 6.6, 8 follow-up, 9.3, 11.3) | Persons 2, 3, 4 |
| Modes | Auto mode only | Plan mode (Phase 11.2) | Person 5 |
| Pause | No new start; a running node runs to its end | A running node is suspended at its next tool boundary (Phase 10.6) | Person 3 |
| Guardrails | Schema and Validator errors, visibility, `REQUIRES_NODE`, the token budget | Resources, mandatory approvals, PHI rules (Phase 11.1, Plan 13) | Persons 5, 4 |
| Privacy | Synthetic or de-identified data only | PHI-Local (Phase 13.3) | Person 4 |
| Agents | `main_agent`, `composer`, the 5 execution agents of item 1, the 2 segmentation internal agents | + `recovery_agent`, `literature_review_agent` and its 5 internal agents | Person 2 |
| Skills | None; the guidance is in the prompts | SDK skills (Phase 3.5) | Person 2 |
| AutoGen | Frozen; `/plan`, `/run`, `/session/{id}/resume-pipeline` return `410 Gone` | Deleted (Plan 17) | Everyone |

## Done when (the whole roadmap)

The first working flow, plus: an `ask_user` cohort approval that suspends and resumes across a restart; a heavy tool as a Procrastinate job; a node with `approval: after`; a recovery proposal; a `phi_local` session; the segmentation client on `MonaiHttpBackend`. First on fakes, then inside Docker. Then Plan 17.2–17.6.

## Frontend: whoever changes the backend changes the frontend

There is no separate frontend plan. The person whose backend change breaks a screen updates that screen. The files are in `Frontend/OHIF-Viewer/extensions/research-playground/src/`:

- **Unchanged:** the result views (cohort, segmentation, features, modeling, report), because the tools keep sending the same view events from their `on_result` hooks; the chat input (`POST /session/{id}/input`); `node_status`; `/pause` and `/stop`.
- **Person 5:** `PlaygroundCanvas.tsx` and `ResearchPlayground.tsx` build the canvas from the Session Graph (`GET /session/{id}/graph` and the `graph_change_*` events) instead of `plan.steps`, and the Plan / Run buttons go; `services/api.ts` reads the graph instead of `plan-state` and `resume-pipeline`; `ChatSidebar.tsx` shows the Main's replies; Plan mode.
- **Person 2:** the cohort approval (`DataCohortView.tsx`) and the segmentation review (`views/segmentation/`) answer `ask_user`.
- **Person 2** also: the graph-change approval card (Phase 11.3).
- **Person 3:** a node waiting for the user, and a seeded rerun, on the canvas.
- **Person 4:** `ClientSettingsPanel.tsx` follows the Model Gateway's per-profile aliases (Phase 9.2 item 5).

## Contracts first

In the first days, each person merges the contracts of their area: the types and function signatures the others call, each with a fake and its tests. Prompts, logic and real backends can be drafts; the shapes cannot.

| Contract | Owner | Where | Fake the others use |
|---|---|---|---|
| `JobClient`, `JobRequest`, `JobRecord`, the job statuses (Phase 5.1) | Person 1 | `jobs_core/` | `jobs_core/backends/in_memory.py` |
| The ports and `graph` visibility of `run_modeling` and the heavy tools (Phases 7.1, 7.3) | Person 1 | `toolpacks/modeling/`, `toolpacks/radiomics/` | the tools themselves against `fake_domain_api` |
| `agent.md` format and loader, the specs, reworked for the SDK (Phase 3.0) | Person 2 | `toolkit/agents/loader.py`, `agentpacks/` | the real loader with draft prompts |
| The `ask_user` tool (Phase 9.3 item 3) | Person 2 | `toolpacks/interaction/` | the real tool on `FakeAgentEngine` with a scripted suspension |
| `MonaiHttpBackend` as a `JobClient` (Phase 15.3) | Person 1 | `jobs_core/backends/monai_http.py` | the in-memory `JobClient` |
| Phase 6.6 shapes: transcript, `pending_tools`, answer, node `approval`, start release, `NEEDS_RELEASE`, `USER_RELEASE_REQUIRED` | Person 3 | `application_api/schemas/`, `clients/harness_client.py`, `execution/graph_store.py` | `InMemoryGraphStore` with the new methods; `fake_domain_api` routes |
| `SessionExecutor`: `start`, `stop`, `wake(reason)`, `pause`, `resume` (Phase 10.2) | Person 3 | `execution/executor.py` | `FakeExecutor` (records the calls) |
| Engine types: `run(..., resume)`, `open_main()` / `turn()`, `AgentStatus.SUSPENDED`, `Suspension`, `Resume` (Phase 9.3) | Person 4 | `toolkit/agents/engine.py` | `FakeAgentEngine` |
| `options_for(profile, ctx) -> ModelOptions` (Phase 9.2) | Person 4 | `harness/model_gateway.py` | `FakeModelGateway` |
| `SeedFrom{run_id, message_uuid}`, `NodeDef.approval` (Plan 8 follow-up) | Person 4 | `planning/graph_model.py`, `application_api/core/graph_content.py` | none needed (pure models) |
| `HttpSessionStore` (Phase 9.5) | Person 4 | `execution/session_store.py` | an in-memory store over `InMemoryGraphStore` |
| Control tools: names, input models, `RunAccepted` (Phase 12.2) | Person 5 | `toolpacks/control/`, `planning/proposals.py` | handlers backed by `InMemoryGraphStore` |
| Inbox item types (Phase 12.1) | Person 5 | `harness/inbox.py` | none (plain models) |
| `guardrails.check(action, ctx) -> Decision` and the action types (Phase 11.1) | Person 5 | `harness/guardrails.py` | `AllowAllGuardrails` |

## Rules for working in parallel

1. **Code against contracts only.** If what you need is not merged yet, use its fake; never import another person's unmerged branch.
2. **A contract change is a small PR** that edits the contract, its fake and the Contracts table above, reviewed by the owner and by the people who use it. Nothing else changes a contract.
3. **One branch per person** off `New-Architecture`: `track/p1-compute`, `track/p2-agents`, `track/p3-execution`, `track/p4-engine`, `track/p5-main`. Small PRs into `New-Architecture`, each passing the deterministic gate; rebase on it often. Nobody pushes to `New-Architecture` directly.
4. **Folders have owners** (table below). A change in another person's folder is a small PR reviewed by that person.
5. **Shared files** take small, additive edits only: `agent_system/app.py` (one router or one lifespan line per PR), `services/event_registry.py` (append event types), `Backend/agent_system/requirements.txt` (UTF-16 LE, CRLF; one package per PR), `deploy/docker-compose.backend.gpu.yml` (one service per PR), and `CLAUDE.md`.
6. **The segmentation path is Person 1's** (the MONAI server, `MonaiHttpBackend`, the segmentation module except `planner.py`, `toolpacks/segmentation/`), and Plan 15 lands first. Person 2's Phase 9.7 edits to `module.py` (the engine calls, the review) come after it, as small PRs reviewed by Person 1.
7. **Migrations:** Person 3 owns migration "t", Person 1 owns migration "s". The one merged second sets its `down_revision` to the head at that time.
8. **`plan.md` and `team-plan.md`:** each person edits only their own plans in `plan.md` (status, as-built notes) and their own rows of the Contracts table here. A change that crosses areas is a small PR reviewed by the people it touches.
9. **Finished early?** Help another area (a small PR reviewed by its owner), or start the next item of your own area. Nobody waits idle.
10. **Done for a PR:** its tests pass with fakes, and it depends on nothing unmerged. A phase of a plan is done when its "Done when" holds; mark it in the Milestones table of `plan.md` in the same PR.

## Folder owners

| Person | Owns |
|---|---|
| **Person 1** | `jobs_core/` (`backends/monai_http.py` included), `compute_worker/`, `toolkit/runtime/job.py`, the job files of `application_api/` (migration "s", the job routes), the training files of `application_api/` (Phase 16.2), `toolpacks/modeling/`, `toolpacks/radiomics/`, `toolpacks/reporting/`, `toolpacks/segmentation/`, the segmentation module (`agents/execution_agents/segmentation/`, except `planner.py`), `Backend/monailabel/`, `Backend/apps/radiology/` trainers, the ingestion code |
| **Person 2** | `agentpacks/`, `skills/`, `toolkit/agents/loader.py`, `toolkit/engines/subagents.py`, `toolkit/delegation/` (until Phase 0.2 deletes it), `toolpacks/bookkeeping/`, `toolpacks/population/`, `toolpacks/literature/`, `toolpacks/interaction/`, the segmentation planner (`agents/execution_agents/segmentation/planner.py`), `harness/approvals.py`, `harness/skills_index.py`; frontend: `DataCohortView.tsx`, `views/segmentation/`, the approval card |
| **Person 3** | `application_api/` (the session-graph, run, attempt and transcript files; migration "t"; `core/execution_rules.py`), `clients/harness_client.py`, `execution/graph_store.py`, `execution/in_memory_graph_store.py`, `execution/executor.py`, `node_runner.py`, `watchdog.py`, `resources.py`, `expectations.py`, `config/resources.yaml` |
| **Person 4** | `toolkit/engines/` (except `subagents.py`), `toolkit/agents/engine.py`, `execution/session_store.py`, `harness/model_gateway.py`, `harness/privacy.py`, `config/models.yaml`, `deploy/litellm/`, `planning/graph_model.py`, `planning/validator.py`, `planning/inputs.py`, `execution/affected.py`, `execution/diff_engine.py`, `application_api/core/graph_content.py`, the agents image dependencies, the CI workflows; frontend: `ClientSettingsPanel.tsx` |
| **Person 5** | `harness/inbox.py`, `main_loop.py`, `session_runtime.py`, `turn_context.py`, `harness/guardrails.py`, `toolpacks/control/`, `planning/proposals.py`, `api/routes/graph.py` and the session routes; frontend: `PlaygroundCanvas.tsx`, `ResearchPlayground.tsx`, `services/api.ts`, `ChatSidebar.tsx` |
