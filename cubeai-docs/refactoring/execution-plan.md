# CUBE.AI Refactoring — Team Execution Plan

**Status:** Draft for team review · **Scope:** planning only, no code changed · **Team size:** 5 developers

Every claim below is annotated with the file that supports it. Where the codebase does not
answer a question, it is marked `NEEDS CLARIFICATION`. Where a choice belongs to the team,
it is marked `NEEDS DECISION`. Non-required improvements are marked `OPTIONAL SUGGESTION`.

---

## 1. Executive Summary

Five workstreams were requested. Inspection shows they are **not equally sized, and two of
them are much further along than the brief assumes**:

| Workstream | Assumed state | Actual state (evidence) |
|---|---|---|
| Redis | "introduce/use Redis" | **Zero Redis anywhere in the backend.** Greenfield. Grep across `Backend/` returns no `redis` import, dependency, or container. |
| WebSocket | "needs cleanup" | Backend WS layer is already hardened and bounded (`socket_store.py`), but **100% process-local**, and the **frontend has two forked, diverged WebSocket singletons**. |
| Orthanc / masks | "masks are in a local folder, move them to Orthanc" | **Masks are already converted to DICOM SEG and uploaded to Orthanc today** (`segmentation/services.py:648-684`, `highdicom`), and registered in the `segmentations` table. What is missing is making Orthanc the *read* path and giving the local copy a session cache lifecycle. |
| Workspace JSON → DB | "eliminate JSON on disk" | A **hybrid already exists**: hypothesis / plan / plan_steps / task_messages / context_augmentations are in PostgreSQL and an MCP tool already reads task context from the DB (`mcp/tools/task_context.py`). Roughly 8 JSON file kinds remain on disk. |
| AutoGen → LangGraph | major migration | Confirmed major. AutoGen is load-bearing across planning, execution graph, tools, and human-in-the-loop. |
| HL7 | "introduce HL7" | **Zero HL7/FHIR references anywhere.** Fully greenfield and under-specified. |

The single largest structural problem found is not on the list: **`Backend/agent_system/app.py`
is an 86 KB / ~2,150-line monolith** holding ~40 REST endpoints, the WebSocket endpoint, the
planner runner, the pipeline runner, review-gate persistence, and file serving. Nearly every
workstream has to edit this one file, which makes five-way parallel work collide by default.
Splitting it is therefore proposed as a **Phase 1 foundation task, not a cleanup task**.

The plan's centre of gravity is: **agree contracts and split the monolith first, then let four
tracks run in parallel behind those seams, and migrate the agent framework last** — because
the LangGraph migration consumes the contracts, the state store, the event transport, and the
segmentation subgraph that the other four tracks produce.

---

## 2. Current Architecture (high level, verified)

### Services

| Service | Source | Port | Owns |
|---|---|---:|---|
| OHIF Viewer (customized) | `Frontend/OHIF-Viewer/` | 3000 | UI, Research Playground canvas |
| Application API | `Backend/application_api/` | 8001 | **Sole** PostgreSQL owner; Orthanc sync |
| Agent System | `Backend/agent_system/` | 8150 | Planning, execution, WS, review gates |
| MCP tool server | `Backend/mcp/` | stdio subprocess | Agent tools |
| MONAI Label | `Backend/monailabel/`, `Backend/apps/radiology/` | 8000 | Inference, training, nnUNet/VISTA/SAM2/VoxTell |
| Orthanc | `Backend/orthanc/` + image | 8042 | DICOM + DICOMweb |
| PostgreSQL 18 | image | 5432 | System of record (16 tables, 26 migrations) |

Ingress is OpenResty; it strips `/api`, `/agents`, `/inference`, `/pacs` prefixes.

### Verified data flow

```
Browser (OHIF + Research Playground)
   │
   ├── REST /agents/*  ──► Agent System app.py (~40 endpoints)
   ├── WS   /ws?session_id=… ──► socket_store.ConnectionManager (in-process)
   ├── REST /api/*     ──► Application API ──► PostgreSQL
   └── REST /inference/* ──► MONAI Label ──► Orthanc, Backend/storage

Agent System ──► Application API (BackendClient, 24 KB) : hypothesis, plan, steps,
                 task messages, context augmentation, ws_log persistence
Agent System ──► MONAI Label : inference/training jobs
Agent System ──► Orthanc directly : DICOM SEG upload (segmentation/services.py)
Agent System ──► MCP stdio subprocesses : env WORKSPACE_DIR / SESSION_ID / AGENT_ID
Orthanc Lua OnStableSeries/OnStableStudy/OnDeleted* ──► POST /sync/orthanc-webhook
```

### Persistence mechanisms in use today

1. **PostgreSQL** — 16 tables: `patients`, `studies`, `volume_series`, `segmentations`,
   `radiomics_features`, `models`, `hypotheses`, `plans`, `plan_steps`, `task_messages`,
   `context_augmentations`, `chat_sessions`, `chat_messages`, `ws_log`, `workflow`,
   `workflow_details`. A JSON column is already in production use
   (`radiomics_features.features`).
2. **Orthanc** — DICOM images and DICOM SEG masks.
3. **Filesystem** — `Backend/storage/`, resolved exclusively through `storage_paths.py`.
4. **Workspace JSON** — under `hypotheses/workspace_<session-id>/state/`.
5. **In-process memory** — `SessionStore._sessions`, `ConnectionManager` dicts,
   `ExecutionCapacity`, `ResourceScheduler`.
6. **Redis** — **does not exist.**

### Agent architecture

- Framework: **Microsoft AutoGen 0.7** (`autogen_agentchat`, `autogen_core`, `autogen_ext`).
  ⚠️ `agent_system/requirements.txt` *also* pins the unrelated AG2 fork (`ag2`, `autogen`,
  `pyautogen` ≥0.10). No code imports it. Dead/conflicting dependency.
- Execution: `teams/execution_team.py` builds a `DiGraphBuilder` → `GraphFlow`, with
  `MessageFilterAgent` + `PerSourceFilter` restricting each agent to its predecessor's last
  message. Agents are registered in a single `AGENT_FACTORIES` dict.
- Planning: `planning_agents/planner.py` (63 KB) + `planning_agents.py`, plus a separate
  `RoundRobinGroupChat` literature-review team (`literature_review_agent.py`, 86 KB).
- Tools: MCP over **stdio subprocesses**, wired with `autogen_ext.tools.mcp.StdioServerParams`;
  per-agent tool allow-lists.
- Human-in-the-loop: two different mechanisms — AutoGen `UserProxyAgent` with an input
  provider bridged to `ws_manager.wait_for_user_message`, **and** REST review gates
  (`/populations/approve-cohort`, `/segmentation-review`, `/segmentation-review/wait/{id}`)
  whose state is persisted as JSON on disk.
- Concurrency: `ExecutionCapacity` (session admission) + `ResourceScheduler`
  (`gpu_segmentation`, `cpu_heavy` leases). Both asyncio, both process-local, **both with clean
  `acquire/release/status` interfaces** — good seams for a Redis-backed implementation.

### Size hot spots (refactoring cost drivers)

| File | Size |
|---|---:|
| `agent_system/app.py` | 86 KB |
| `literature_review_agent/literature_review_agent.py` | 86 KB |
| `literature_review_agent/knowledge_builder.py` | 77 KB |
| `execution_agents/segmentation/utils.py` | 64 KB |
| `planning_agents/planner.py` | 63 KB |
| `execution_agents/segmentation/module.py` | 61 KB |
| `execution_agents/segmentation/services.py` | 60 KB |
| `tools/classification_tool.py` | 58 KB |
| `services/session_service.py` | 39 KB |
| `Frontend/.../research-playground/src` | 12,773 lines, **0 tests** |

---

## 3. Major Workstreams

| ID | Workstream | People | Framing after inspection |
|---|---|---:|---|
| **WS-A** | Agentic: AutoGen → LangGraph | 5 | Confirmed largest. Consumes output of B/C/D. |
| **WS-B** | Orthanc / PACS / masks / NIfTI | 2 | Smaller than assumed for masks, larger than assumed for NIfTI→DICOM (does not exist). |
| **WS-C** | Database / workspace JSON | 1 | Inventory + decision + migration. Feeds WS-A state schema. |
| **WS-D** | Redis / WebSocket | 1 | Redis is greenfield; WS refactor spans backend **and** two forked frontend clients. |
| **WS-E** | HL7 | 0 dedicated | Positioning only this cycle. Too under-specified to staff. |
| **WS-F** | *(added)* Monolith decomposition & contracts | shared | Not requested, but every other workstream edits `app.py`. Must be a foundation. |

---

## 4. Recommended Team Structure

### Assessment of the proposed distribution

The brief proposes: Dev1 DB, Dev2 Redis/WS, Dev3+Dev4 Orthanc/Masks, Dev5 flexible.
**Three adjustments are recommended, each for a codebase-visible reason:**

1. **Two developers on masks is over-staffed; NIfTI→DICOM is under-staffed.** Masks already
   reach Orthanc as DICOM SEG (`services.py:648-684`). The remaining mask work is
   read-path + cache lifecycle. Conversely, *no* NIfTI→DICOM image conversion exists anywhere —
   every NIfTI path in the repo is DICOM→NIfTI. Recommend splitting WS-B into
   **mask lifecycle (1 dev)** and **NIfTI ingestion (1 dev)**.
2. **WebSocket refactoring is not a one-person job as scoped.** It spans `socket_store.py`,
   the WS endpoint in `app.py`, `pipeline_monitor.py`'s event synthesis, **and two forked
   frontend clients** (`platform/app/src/utils/WebsocketService.ts`, 189 lines vs
   `extensions/research-playground/src/utils/WebsocketService.ts`, 267 lines — diverged; only
   the extension copy has replay buffering). Recommend the Redis/WS owner keeps the
   backend + Redis, and the **frontend consolidation is a separate, explicitly assigned task**.
3. **"Flexible" is too vague for Dev 5 given `app.py`.** Recommend Dev 5 owns
   the contract package, the `app.py` decomposition, and integration — the three things
   everyone else is blocked by.

`NEEDS CLARIFICATION` — **No developer is assigned to the frontend.** The Research Playground
is 12,773 lines with zero tests, and the contract/WS work changes its inputs. Who owns it?

### Recommended matrix

| Dev | Main responsibility | Secondary (Agentic track) | Key deliverables |
|---|---|---|---|
| **D1** | WS-C — Database & workspace state | **A2** LangGraph state schema & checkpointing | Workspace JSON inventory; persistence decision dossier; new schema + migrations; state store behind an interface |
| **D2** | WS-D — Redis & WebSocket backend | **A4** Streaming, events & HITL interrupts | Redis topology; Redis-backed schedulers; WS transport refactor; typed event catalogue |
| **D3** | WS-B1 — Orthanc, PACS & mask lifecycle | **A5** Segmentation subgraph | Mask read-from-Orthanc facade; session cache + cleanup; Orthanc client consolidation |
| **D4** | WS-B2 — NIfTI → DICOM ingestion | **A3** Tool / MCP adapter layer | NIfTI→DICOM converter + provenance metadata; ingestion endpoint; tool contract layer |
| **D5** | WS-F — Contracts, `app.py` decomposition, integration | **A1** Graph & orchestration core; WS-E HL7 positioning | Shared contract package; router split; LangGraph skeleton; integration harness; HL7 position paper |

Every developer owns the tests for their own slice. D5 owns the cross-cutting integration suite.

---

## 5. Agentic Team Breakdown (AutoGen → LangGraph)

The goal is parallel work with minimal blocking. The codebase already provides five
**natural seams** that map cleanly onto five people:

| Track | Area | Owner | Current code it replaces/wraps | Why this owner |
|---|---|---|---|---|
| **A1** | Graph & orchestration core | D5 | `teams/execution_team.py` (`AGENT_FACTORIES`, `DiGraphBuilder`, `GraphFlow`, `MessageFilterAgent`); `run_pipeline` in `api/runners.py` (moved out of `app.py` by CON-2) | Owns the contracts and the monolith split; the graph is the integration point |
| **A2** | State schema & checkpointing | D1 | `models/session.py`, `services/session_store.py`, `services/execution_state.py`, `resume_recovery.py`, workspace `state.json` / `scratchpad.json` | LangGraph state *is* the workspace-JSON migration target |
| **A3** | Tools & MCP adapter | D4 | `autogen_ext.tools.mcp` wiring in all six agent factories; `Backend/mcp/tools/**` | Tool layer is where NIfTI/ingestion tools land too |
| **A4** | Streaming, events, HITL | D2 | `view_emitter.py`, `pipeline_monitor.py`, `UserProxyAgent` input bridge, REST review gates | LangGraph streaming + interrupts terminate on the WS transport D2 owns |
| **A5** | Segmentation subgraph | D3 | `execution_agents/segmentation/**` (185 KB across module/services/utils/planner) + long-running MONAI jobs | Largest and most Orthanc-coupled agent; same person owns Orthanc |

**Shared, non-negotiable, agreed in Phase 1 before any track starts:** the agent I/O contract
package (§6, `CON-1`) and the state schema (`CON-3` → `AG-2`). These two are what make the five
tracks independent.

**Reusable assets — do not rewrite from scratch:**
- `models/plans.py` — `ClinicalHypothesis`, `StudyPlan`, `StudyStep`, `TaskMessage`,
  `ContextAugmentation` are already clean Pydantic models.
- `models/tool_logging.py` — `ToolEvent` is already a typed, versioned event.
- `execution_agents/segmentation/contracts.py` — a mature, versioned Pydantic contract set
  (`CURRENT_SCHEMA_VERSION = 2`) that should be the **template** for the shared contract package.

`NEEDS DECISION` — **Does every AutoGen concept get a LangGraph equivalent, or is some of it
deleted?** Specifically: `MessageFilterAgent`/`PerSourceFilter` (LangGraph state is explicit,
so message filtering may become unnecessary), and the `RoundRobinGroupChat` literature team
(no direct LangGraph primitive; candidates are a subgraph loop or a plain sequential chain).

---

## 6. Unified Agent I/O Contracts

### `CURRENT CONTRACT` — what exists today

**Typed and reusable:**
- `models/plans.py` — hypothesis, plan, step, task message, context augmentation.
- `models/tool_logging.py` — `ToolEvent` with `EventType` and `ContentPayload` unions.
- `models/requests.py` — `PlanningRequest`, `SessionControlRequest`, `WebSocketBroadcastRequest`,
  with a shared `SESSION_ID_PATTERN`.
- `segmentation/contracts.py` — versioned, validated, with cross-reference validators.
- WS envelope, built in `socket_store.build_event`:
  `{version, event_id, sequence, session_id, run_id, type, payload, timestamp, replaying}`.

**Untyped — the actual contract gap:**
- **Every view payload is `Dict[str, Any]`** (`view_emitter.py`): `population_output`,
  `segmentation_output`, `features_output`, `modeling_output`, `report_output`, `latex_output`.
- **Event vocabulary is informal and has drifted.** The frontend handles ~40 event type
  strings; grep shows `study_complete`, `node` and `hypothesis_approval` have **no backend
  emitter** — dead handlers or dynamically-typed emissions.
- **`pipeline_monitor.py` synthesises event types by substring-matching the agent source name**
  (`if "population" in source.lower(): msg_type = "population_output"`) and then **reads
  `outputs/{agent_id}_output.json` off disk** to build the payload. This is the single tightest
  coupling in the system: it binds the agent layer, the workspace-JSON layer, the WS layer and
  the frontend together.
- Agent handoff payload is `TaskMessage.payload: Dict[str, Any]` — untyped per agent.
- Agent output files are validated loosely in `mcp/tools/agent_output.py`.

### `PROPOSED CONTRACT` — strategy

A single versioned contract package, owned by D5, consumed by everyone:

1. **One source of truth**, shipped as an importable Python package and a generated TypeScript
   type module, so frontend and backend cannot drift. `NEEDS DECISION` — generation mechanism
   (JSON Schema export from Pydantic + codegen, or OpenAPI; note `openapi-core`,
   `openapi-pydantic` and `jsonschema` are already dependencies).
2. **Close the event catalogue.** Every WS `type` is an enum member with a typed payload model.
   Unknown types are rejected in dev, logged in prod.
3. **Type the six view payloads** — this is what lets the frontend developer work against a
   stub while the agents are being rewritten.
4. **Per-agent typed I/O**: replace `TaskMessage.payload: Dict[str, Any]` with a discriminated
   union keyed on `agent_id`, following the `segmentation/contracts.py` pattern.
5. **Version everything** — `schema_version` on persisted contracts, as `contracts.py` already does.
6. **Stop synthesising events from source-name substrings.** Agents emit typed events;
   `pipeline_monitor` routes them without inspecting the filesystem.

---

## 7. Work Breakdown Structure

Status of every task below is `TODO`.

### Phase 0 — Discovery

---
**DISC-1 · Workspace JSON inventory** · Owner **D1**
Objective: For each JSON kind (`state.json`, `scratchpad.json`, `config.json`,
`segmentation_manifest.json`, `review_draft.json`, `review_result.json`,
`literature_context.json`, `outputs/{agent_id}_output.json`, `clinical_data.json`) record:
purpose, producer, consumers, lifecycle, classification (session state / persistent state /
config / cache), and whether it duplicates a PostgreSQL table.
Depends on: —  ·  Parallel with: DISC-2..5
Deliverable: inventory table + duplication map. Start from `storage_paths._STATE_FILES` and
`_WORKSPACE_DIR_MAP` (the single mapping point) and the `workspace_subpath` call sites.

---
**DISC-2 · WebSocket & event surface audit** · Owner **D2**
Objective: Map WS entry points, connection lifecycle, background tasks, session handling,
duplicated logic, error handling; produce the definitive list of emitted vs handled event types.
Depends on: —  ·  Parallel with: DISC-1,3,4,5
Deliverable: event catalogue (emitters ↔ handlers ↔ orphans) + duplication report covering the
two forked frontend `WebsocketService` files.

---
**DISC-3 · Orthanc / mask flow audit** · Owner **D3**
Objective: Trace mask generation → DICOM SEG → Orthanc → DB (`segmentations`) → downstream
consumers. Identify every place a mask is read from the local workspace instead of Orthanc.
Depends on: —  ·  Parallel with: DISC-1,2,4,5
Deliverable: mask lifecycle diagram; list of local-read sites (start with
`feature_extraction_agent` `mask_dir`, `segmentation/utils.py:570,1295,1548`).

---
**DISC-4 · Image ingestion & NIfTI audit** · Owner **D4**
Objective: Document the current ingestion paths (Orthanc upload, Lua webhook, DICOMweb
download-to-NIfTI in `monailabel/endpoints/my_endpoints_datastore.py`) and confirm what does
**not** exist. Inventory available libraries (`highdicom`, `pydicom`, `SimpleITK`, `nibabel`,
`pynrrd` are already dependencies).
Depends on: —  ·  Parallel with: DISC-1,2,3,5
Deliverable: ingestion flow map + NIfTI→DICOM feasibility note with library options.

---
**DISC-5 · AutoGen dependency map** · Owner **D5** (all devs contribute their slice)
Objective: Enumerate every AutoGen touchpoint and classify each as *direct migration*,
*redesign*, *shared infrastructure*, or *delete*. Must cover: `GraphFlow`/`DiGraphBuilder`,
`MessageFilterAgent`/`PerSourceFilter`, `AssistantAgent`, `UserProxyAgent`,
`RoundRobinGroupChat`, `CancellationToken`, `mcp_server_tools`/`StdioServerParams`, `Console`.
Depends on: —  ·  Parallel with: DISC-1..4
Deliverable: classification table; explicit list of AutoGen concepts with **no** LangGraph
equivalent. Also resolve the `ag2`/`pyautogen` vs `autogen-agentchat` dependency conflict.

---

### Phase 1 — Contracts & Foundations *(blocking; nothing else starts until these land)*

---
**CON-1 · Shared agent I/O contract package** · Owner **D5**
Objective: Ship the versioned contract package of §6 — event enum, typed view payloads,
per-agent task/output models, generated frontend types.
Depends on: DISC-1, DISC-2, DISC-5  ·  Parallel with: CON-2
Deliverable: importable package + generated TS types + contract tests. **Blocks: all of WS-A,
all frontend work, DB-2, RW-2.**

---
**CON-2 · `app.py` decomposition** · Owner **D5**
Objective: Split the 2,150-line monolith into routers by domain (planning, execution control,
session, populations, segmentation review, reports, client config, events, WS) with no
behaviour change. Purpose is to remove the five-way merge conflict, not to redesign.
Depends on: —  ·  Parallel with: CON-1
Deliverable: router modules + unchanged endpoint surface + green deterministic suite.
**Blocks: everything that edits `app.py` — i.e. most tasks.**
**Status: `DONE`** — see [con-2-app-decomposition.md](./con-2-app-decomposition.md).

---
**CON-3 · Persistence decision dossier** · Owner **D1**
Objective: Present Option A (PostgreSQL) vs Option B (MongoDB) with evidence, for a **team
decision**. Do not decide unilaterally.
Depends on: DISC-1  ·  Parallel with: CON-1, CON-2, CON-4
Deliverable: comparison document + recommendation + migration cost estimate. `NEEDS DECISION`.
Evidence to include (already gathered): the Application API is architecturally the **sole**
DB owner and enforces it; PostgreSQL already stores JSON (`radiomics_features.features`);
26 Alembic migrations exist; adding MongoDB introduces a second datastore into an architecture
that documents "PostgreSQL remains the only application datastore".

---
**CON-4 · Redis topology & scope decision** · Owner **D2**
Objective: Decide *what* Redis is used for and *how* it is deployed, before writing code.
Depends on: DISC-2  ·  Parallel with: CON-1, CON-2, CON-3
Deliverable: topology doc + compose service definition. `NEEDS DECISION` on each of:
session/WS routing (pub-sub), task queues (GPU/CPU), short-term agent memory,
LangGraph checkpointer backend, persistence mode (AOF/RDB/none), single vs clustered.
**Constraint found in code:** `Session` (`models/session.py`) holds live `asyncio.Event`,
`asyncio.Queue` and `asyncio.Task` objects. These **cannot** be serialised to Redis. Any
"session state in Redis" must first split serialisable state from runtime handles.

---
**CON-5 · LangGraph state schema** · Owner **D1**, reviewed by **D5**
Objective: Define the LangGraph state object and its checkpointing strategy.
Depends on: DISC-1, DISC-5, CON-1, CON-3
Parallel with: — (this is the agentic critical path)
Deliverable: state schema + checkpointer choice. **Blocks: AG-1..AG-5.**

---

### Phase 2 — Parallel Development

#### WS-D — Redis / WebSocket · **D2**

- **RW-1 · Redis service + client foundation.** Depends: CON-4. Parallel: everything.
  Deliverable: Redis in compose, connection module, health check, local dev story.
- **RW-2 · WS transport refactor.** Depends: CON-1, CON-2, DISC-2. Parallel: RW-3, OR-*, DB-*.
  Objective: separate transport / routing / session lifecycle / message handling in
  `socket_store.py`; keep the existing bounded queues, dedupe and backpressure behaviour —
  they are already correct. Deliverable: layered WS module + unchanged external envelope.
- **RW-3 · Redis-backed schedulers.** Depends: RW-1. Parallel: RW-2.
  Objective: back `ExecutionCapacity` and `ResourceScheduler` with Redis behind their existing
  interfaces. **These two classes already have clean `acquire/release/status` seams** — this is
  the lowest-risk, highest-value Redis task and needs no coordination with other tracks.
- **RW-4 · Cross-process session routing.** Depends: RW-1, RW-2. Parallel: OR-*, DB-*.
  Objective: Redis pub/sub so WS events reach a session's sockets from any worker.
  Deliverable: multi-worker-capable routing. `NEEDS CLARIFICATION` — is multi-worker actually a
  goal, or is single-worker acceptable? This task's value depends entirely on the answer.
- **RW-5 · Frontend WebSocket consolidation.** Depends: CON-1, RW-2. Owner: `NEEDS CLARIFICATION`
  (no frontend dev assigned). Objective: collapse the two forked `WebsocketService` singletons
  into one, keeping the replay buffering that only the extension copy has; introduce a single
  typed event reducer so the six components that subscribe to the raw socket stop parsing events
  independently.

#### WS-C — Database / Workspace · **D1**

- **DB-1 · Schema design for workspace state.** Depends: CON-3 (decision made). Parallel: OR-*, RW-*.
- **DB-2 · State-access interface.** Depends: CON-1, DB-1. Parallel: OR-*, RW-*.
  Objective: put every workspace-state read/write behind one interface so the storage backend
  can change without touching agents. **Must cover `pipeline_monitor.py`'s direct disk reads** —
  that is the hardest consumer.
- **DB-3 · Dual-write / shadow phase.** Depends: DB-2. Objective: write both to JSON and to the
  chosen store, compare, prove equivalence before switching reads.
- **DB-4 · Migration + backfill tooling.** Depends: DB-1. Note: `Backend/scripts/migrate_storage.py`
  and `migrate_storage.ps1` are an existing, working precedent for a safe storage migration —
  reuse their conflict/rollback conventions (`_migrated-legacy/`).

#### WS-B1 — Orthanc / masks · **D3**

- **OR-1 · Orthanc client consolidation.** Depends: DISC-3. Parallel: everything.
  Objective: one Orthanc client. Today there are at least three call sites/styles —
  `application_api/services/orthanc_client.py` (aiohttp), `segmentation/services.py` (httpx,
  direct `POST /instances`, `/tools/find`), `segmentation/utils.py:768`.
- **OR-2 · Mask access facade.** Depends: CON-1, OR-1. Parallel: DB-*, RW-*.
  Objective: a single `get_mask(session, series_uid)` seam so consumers stop reading
  `workspace/masks/` directly. **This is the enabling task for the whole mask migration.**
- **OR-3 · Orthanc-first mask retrieval.** Depends: OR-2. Objective: fetch DICOM SEG from
  Orthanc and materialise the NIfTI the radiomics tools need, instead of trusting the workspace copy.
- **OR-4 · Session cache lifecycle.** Depends: OR-2, OR-3. Objective: local mask cache scoped to
  the session, deleted at session end, Orthanc remaining permanent.
  `NEEDS CLARIFICATION` — what exactly ends a session? `WS_SESSION_IDLE_TTL_SECONDS` (WS idle),
  pipeline completion, and explicit user close are three different events in the current code.
- **OR-5 · Mask provenance & reconciliation.** Depends: OR-3. Objective: harden the existing
  deterministic-SEG-UID reconciliation (`_find_existing_segmentation_instance`) and the direct
  sync fallback (`_sync_uploaded_segmentation_series`), which currently exists because the Lua
  webhook can time out while MONAI is busy.

#### WS-B2 — NIfTI → DICOM · **D4**

- **NI-1 · Conversion approach.** Depends: DISC-4. Deliverable: chosen approach + spike.
  `NEEDS DECISION` — library/approach. Available today without new dependencies: `pydicom`,
  `highdicom`, `SimpleITK`, `nibabel`. Note `highdicom` is already used for SEG creation but
  SEG creation is **not** the same problem as synthesising a primary image series from a NIfTI.
- **NI-2 · Provenance metadata.** Depends: NI-1, CON-1. Objective: generic tags marking DICOM as
  NIfTI-derived. `NEEDS DECISION` — which tags/UIDs; how patient/study identity is assigned when
  the NIfTI carries none. This is a clinical-safety-relevant choice, not a coding detail.
- **NI-3 · Ingestion endpoint.** Depends: NI-1, NI-2, CON-2, OR-1. Objective: receive NIfTI →
  convert → store via the existing Orthanc flow so the Lua webhook and DB sync work unchanged.
- **NI-4 · Round-trip validation.** Depends: NI-3. Objective: geometry/orientation/spacing
  preservation. Reuse the existing precedent: `monailabel/endpoints/norm_align.py` already does
  affine/spacing/origin checks, and `tests/regression/radiology/test_voxtell_geometry.py`
  already tests orientation.

#### WS-A — Agentic (all five, after CON-5)

- **AG-1 · LangGraph orchestration core.** Owner **D5**. Depends: CON-1, CON-5.
  Deliverable: graph skeleton + node registry replacing `AGENT_FACTORIES`, with the same
  "add one entry to register an agent" property the current code has.
- **AG-2 · State & checkpointing.** Owner **D1**. Depends: CON-5, DB-2.
- **AG-3 · Tool / MCP adapter.** Owner **D4**. Depends: CON-1, AG-1.
  `NEEDS DECISION` — keep MCP stdio subprocesses, or move to in-process tools? Current design
  passes `WORKSPACE_DIR`/`SESSION_ID`/`AGENT_ID` as subprocess env vars; that mechanism breaks
  if workspace state moves out of the filesystem (DB-2).
- **AG-4 · Streaming, events & HITL.** Owner **D2**. Depends: CON-1, AG-1, RW-2.
  Objective: map LangGraph streaming to the typed event catalogue and LangGraph interrupts to
  the existing review gates. Must unify **two** current HITL mechanisms (UserProxyAgent bridge
  and REST review gates).
- **AG-5 · Segmentation subgraph.** Owner **D3**. Depends: AG-1, AG-3, OR-2.
  Largest single migration (~185 KB). Includes progressive/partial completion, cancellation,
  checkpoint safety and resource leases — all currently covered by regression tests that must
  keep passing.
- **AG-6 · Planning & literature subgraph.** Owner **D5** + **D1**. Depends: AG-1, AG-2.
  Covers `planner.py` (63 KB) and the `RoundRobinGroupChat` literature team.

#### WS-E — HL7 · **D5**, positioning only

- **HL7-1 · Clinical data flow map.** Depends: DISC-3, DISC-4, DISC-1.
  Objective: where clinical information enters, moves and leaves today.
- **HL7-2 · Integration position paper.** Depends: HL7-1. Deliverable: candidate integration
  points, affected components, impact on DB/agents/PACS/WS, and the decision list below.
  **No implementation this cycle.**

---

### Phase 3 — Integration · **D5** coordinates

- **INT-1** Contract conformance across backend ↔ frontend ↔ DB (depends: CON-1 + all Phase 2).
- **INT-2** Redis + WS + agent streaming end-to-end (RW-4, AG-4).
- **INT-3** Orthanc masks + agent pipeline (OR-4, AG-5).
- **INT-4** New persistence + agent state (DB-3, AG-2).

### Phase 4 — Migration (cutover, one at a time, each behind a flag)

`MIG-1` schedulers → Redis · `MIG-2` workspace JSON → chosen store · `MIG-3` mask reads →
Orthanc-first · `MIG-4` NIfTI ingestion enabled · `MIG-5` AutoGen → LangGraph.

`NEEDS DECISION` — big-bang vs strangler for MIG-5. The `AGENT_FACTORIES` registry makes a
per-agent strangler technically feasible; running both frameworks concurrently is the cost.

### Phase 5 — Cleanup *(only after Phase 6 validation)*

`CLN-1` remove AutoGen + the dead `ag2`/`pyautogen` pins · `CLN-2` remove JSON writers ·
`CLN-3` remove local-mask read paths · `CLN-4` delete the superseded frontend WS client ·
`CLN-5` refresh `cubeai-docs/` (several pages are already stale — see `CLAUDE.md`).

### Phase 6 — Testing

See §11.

---

## 8. Dependency Graph

Only dependencies that are visible in the architecture or required by an explicit requirement.

```
DISC-1 ─┬─► CON-1 ──┬─► AG-1 ──┬─► AG-3 ──► AG-5
DISC-2 ─┤           │          ├─► AG-4
DISC-5 ─┘           │          └─► AG-6
                    ├─► RW-2 ──┬─► RW-4
                    │          └─► RW-5
                    ├─► DB-2 ──┬─► DB-3
                    │          └─► AG-2
                    ├─► OR-2 ──┬─► OR-3 ──► OR-4
                    │          └─► AG-5
                    └─► NI-2 ──► NI-3 ──► NI-4

CON-2 (app.py split) ─────► [most tasks that touch endpoints]
DISC-1 ──► CON-3 ──► DB-1 ──► DB-2 / DB-4
DISC-2 ──► CON-4 ──► RW-1 ──┬─► RW-3        (independent of everything agentic)
                            └─► RW-4
CON-1 + CON-3 + CON-5 ──► AG-2
DISC-3 ──► OR-1 ──► OR-2 / NI-3
DISC-3 + DISC-4 + DISC-1 ──► HL7-1 ──► HL7-2
```

**Deliberately *not* marked as dependencies** (they are commonly assumed but the codebase does
not support them):

- *Redis does not block the LangGraph migration.* LangGraph checkpointing could use PostgreSQL;
  the schedulers are behind interfaces. Coupling them would serialise two tracks for no reason.
- *NIfTI→DICOM does not block masks.* Different code paths, different libraries, no shared state.
- *HL7 blocks nothing this cycle* and nothing blocks HL7-1.
- *The database decision does not block the Redis work* — `RW-1`/`RW-3` are self-contained.

**Critical path:** `DISC-1/2/5 → CON-1 → CON-5 → AG-1 → AG-3/AG-4 → AG-5 → MIG-5`.
Everything else has slack. Protect this path.

---

## 9. Recommended Execution Order

**Stage 1 — Discovery (all 5, parallel).** DISC-1..5. Ends with a shared architecture review.
Sequential only in that nothing else should start.

**Stage 2 — Foundations (partly parallel, mostly blocking).**
`CON-2` (app.py split) should land **first and fast** — it is what stops five people colliding.
`CON-1`, `CON-3`, `CON-4` in parallel. `CON-5` last, since it consumes CON-1 and CON-3.
Integration point: **contract freeze review** — all five sign off on CON-1.

**Stage 3 — Parallel development (maximum parallelism, ~4 independent tracks).**
- D2: RW-1 → RW-3 (no cross-track coordination needed — start here for an early Redis win)
- D3: OR-1 → OR-2 → OR-3
- D4: NI-1 → NI-2 → NI-3
- D1: DB-1 → DB-2
- D5: AG-1 skeleton
Integration point: **facade review** — OR-2 and DB-2 must be seams the agentic work can build on.

**Stage 4 — Agentic migration (all 5, coordinated).**
AG-1 first, then AG-2/AG-3/AG-4 in parallel, then AG-5 and AG-6.
Integration point: **weekly graph sync**, because all five now edit one graph definition.

**Stage 5 — Integration.** INT-1..4.

**Stage 6 — Migration cutovers.** One at a time, each behind a flag, each independently
reversible. Suggested order (lowest risk first): MIG-1 → MIG-3 → MIG-4 → MIG-2 → MIG-5.

**Stage 7 — Cleanup, then documentation refresh.**

---

## 10. Milestones & Deliverables

| # | Milestone | Exit criteria |
|---|---|---|
| **M1** | System understood | DISC-1..5 complete; AutoGen classification table agreed; workspace JSON inventory reviewed |
| **M2** | Contracts frozen & monolith split | CON-1 published and imported by both sides; `app.py` split with green tests; persistence + Redis decisions **made and recorded** |
| **M3** | Foundations running in parallel | Redis live with Redis-backed schedulers; mask facade in place; NIfTI conversion spike proven; state-access interface merged |
| **M4** | LangGraph skeleton executes a real pipeline | One end-to-end run on LangGraph behind a flag, AutoGen still default |
| **M5** | Storage & infrastructure migrated | Masks read Orthanc-first with session cache; workspace JSON in dual-write and proven equivalent; NIfTI ingestion available |
| **M6** | HL7 positioned | Position paper + decision list; **no implementation** |
| **M7** | System integrated | INT-1..4 green; both frameworks pass the same suite |
| **M8** | Migrated, validated, cleaned | Cutovers complete; AutoGen and dead paths removed; docs refreshed |

---

## 11. Testing Strategy

**What exists:** 52 backend test files with strict markers (`unit`, `contract`, `integration`,
`pipeline`, `regression`, `radiology`, `e2e`, `docker`, `live_llm`, `gpu`, `slow`), a Docker-based
runner (`Backend/tests/run_tests.ps1`), 3 CI jobs, a storage-boundary gate, and a read-only
load-test harness (`Backend/tests/load/`).

**What is missing, and matters here:** the Research Playground has **zero tests** across 12,773
lines. Every contract change lands there untested.

| Layer | Approach |
|---|---|
| Unit | Per-track, owned by each developer. Keep the existing isolation rules (no network, no DB, `tmp_path` only). |
| Contract | Extend `Backend/tests/contracts/`, which already tests `TaskMessage` and agent-output round-trips. Every CON-1 model gets a round-trip test. Add a **frontend↔backend contract test** so the generated TS types cannot drift. |
| Integration | Extend `tests/integration/` (opt-in via `RUN_BACKEND_INTEGRATION=1`). Add Redis, Orthanc mask round-trip, NIfTI→DICOM→Orthanc→DB. |
| Pipeline | `tests/pipeline/` already runs the real `run_pipeline` with substituted model/graph/monitor/WS. **This is the highest-value existing asset for the LangGraph migration** — it should pass against both frameworks. |
| Migration | Dual-write equivalence (DB-3); mask parity local vs Orthanc; NIfTI geometry round-trip (`NI-4`). |
| Regression | The 12 agent-system regression tests encode hard-won behaviour (cancellation, timeouts, session async safety, segmentation hardening, storage-path traversal). **Treat them as the migration acceptance suite — they must pass unchanged.** |
| Frontend | `OPTIONAL SUGGESTION` — add Jest coverage for the consolidated WS client and the event reducer before RW-5 changes them. There is a precedent: `WebsocketService.test.js` already exists for the platform copy. |
| Load | Re-run `Backend/tests/load/run.ps1 -Profile smoke` before/after each cutover. Baselines exist in `cubeai-docs/architecture/load-test-decision.md`. |

---

## 12. Migration Strategy

Five principles, each grounded in something the repo already does:

1. **Interface first, implementation second.** OR-2, DB-2 and the scheduler interfaces let the
   backend change without touching consumers.
2. **Dual-write then dual-read then cut.** Never delete the old path until the new one is proven.
3. **Flag every cutover.** Each of MIG-1..5 independently reversible.
4. **Reuse the existing migration precedent.** `Backend/scripts/migrate_storage.py` already
   implements a safe storage migration with conflict preservation in `_migrated-legacy/` and a
   boundary checker. Follow those conventions.
5. **Regression suite is the acceptance gate**, not a formality.

---

## 13. Risks

| Risk | Evidence | Mitigation |
|---|---|---|
| **Five developers editing one 2,150-line file** | `app.py` holds ~40 endpoints + WS + runners | `CON-2` first, before anything else |
| **`pipeline_monitor` couples four layers at once** | Infers event type from source substrings, reads `outputs/*.json` from disk | Explicit joint task between D1, D2, D5; do not let either track fix it alone |
| **Frontend has no owner and no tests** | 12,773 lines, 0 tests, 2 forked WS clients, ~40 event types | `NEEDS CLARIFICATION` on ownership; add tests before RW-5 |
| **Session state cannot simply move to Redis** | `Session` holds live `asyncio.Event`/`Queue`/`Task` | Split serialisable state from runtime handles in CON-4 |
| **Segmentation subgraph is very large** | ~185 KB, most Orthanc-coupled, heavy regression coverage | Migrate last (AG-5); keep regression suite as gate |
| **LangGraph may lack equivalents** | `MessageFilterAgent`, `PerSourceFilter`, `RoundRobinGroupChat` | Resolve in DISC-5 *before* CON-5 |
| **Conflicting AutoGen dependencies** | `ag2`/`autogen`/`pyautogen` ≥0.10 pinned alongside `autogen-agentchat` 0.7; no code uses the former | Resolve in DISC-5; remove dead pins |
| **HL7 is unspecified** | Zero references; no stated version, direction, or trigger | Positioning only; do not staff implementation until decisions are made |
| **Single-worker constraint may silently persist** | Everything process-local; docs say run one worker | Make multi-worker an explicit goal or explicitly out of scope (RW-4) |
| **Clinical safety of synthesised DICOM** | NI-2 assigns identity to NIfTI with no patient context | Requires clinical review, not just code review |

---

## 14. NEEDS CLARIFICATION

1. **Frontend ownership.** No developer is assigned; the work is substantial and untested.
2. **HL7 — everything.** Version (v2.x / v3 / FHIR)? Which messages (ADT / ORM / ORU / MDM)?
   Inbound, outbound, or both? Transport (MLLP / REST)? Which external system? What triggers a
   message? Nothing in the codebase constrains any of this.
3. **Is multi-worker deployment a goal?** Determines whether RW-4 is essential or optional.
4. **What ends a session?** WS idle TTL, pipeline completion, and explicit close are three
   different events today. OR-4 cannot be specified without this.
5. **"Interactive steering" — what does it mean concretely?** Pause/resume exists; mid-run plan
   editing does not. Which behaviours are in scope?
6. **Timeline and capacity.** No dates given, so the plan is ordered by dependency, not calendar.
7. **Retention policy** for masks, sessions, workspaces, and `ws_log`.
8. **Regulatory/clinical constraints** on synthesised DICOM and on agent-generated reports.
9. **Is the literature-review subsystem in scope?** 163 KB across two files, invoked optionally.
10. **Target scale** — concurrent sessions, users, study volume. Sizes the Redis work.

## 15. NEEDS DECISION

1. Persistence for workspace state: **PostgreSQL vs MongoDB** (CON-3).
2. Redis scope: which of session routing / task queues / agent memory / checkpointing (CON-4).
3. LangGraph checkpointer backend (CON-5).
4. NIfTI→DICOM library and approach (NI-1).
5. DICOM provenance tags and identity assignment for NIfTI-derived studies (NI-2).
6. Keep MCP stdio subprocesses or move in-process (AG-3).
7. Fate of `MessageFilterAgent`/`PerSourceFilter` and `RoundRobinGroupChat` (DISC-5).
8. Strangler vs big-bang for the LangGraph cutover (MIG-5).
9. Contract type-generation mechanism for the frontend (CON-1).
10. Single-worker vs multi-worker target (RW-4).

## 16. OPTIONAL SUGGESTIONS

1. **Split the oversized modules while migrating**, not after — `segmentation/utils.py` (64 KB),
   `services.py` (60 KB), `module.py` (61 KB), `planner.py` (63 KB) will be touched anyway.
2. **Remove the dead `ag2`/`autogen`/`pyautogen` pins now** — trivial, and removes real confusion.
3. **Add frontend tests before the WS consolidation**, using the existing
   `WebsocketService.test.js` as the pattern.
4. **Promote `segmentation/contracts.py` to the house style** for all contracts — it already has
   versioning, validators and cross-reference checks.
5. **Add a CI job that fails on contract drift** between generated TS types and Pydantic models.
6. **Delete the orphan frontend event handlers** (`study_complete`, `node`,
   `hypothesis_approval`) or implement their emitters — currently they are silent dead code.
7. **Refresh `cubeai-docs/`** as part of M8; several pages already contradict the code
   (see the Gotchas section of `CLAUDE.md`).
8. **Consider an architecture decision record per `NEEDS DECISION` item**, following the existing
   precedent in `cubeai-docs/architecture/load-test-decision.md`.
