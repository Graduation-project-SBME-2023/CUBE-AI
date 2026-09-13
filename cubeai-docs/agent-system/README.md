# 🤖 Agent System Documentation

The Agent System is the AI research pipeline backend that powers the Research Playground. It coordinates multiple specialized LLM-backed agents to autonomously conduct radiomics research studies.

---

## Overview

The pipeline is a **Multi-Agent System (MAS)** where each agent has a clearly defined responsibility, input contract, and output contract. Agents share state through a **workspace directory** and a `scratchpad.json` file.

---

## Architecture

```
User Research Question (via Chat)
          │
          ▼
  ┌───────────────────┐
  │  Hypothesis       │  ← Formulates the research hypothesis
  │  Architect        │
  └─────────┬─────────┘
            │ hypothesis JSON
            ▼
  ┌───────────────────┐
  │  Pipeline         │  ← Selects & sequences agents
  │  Architect        │
  └─────────┬─────────┘
            │ pipeline plan (steps/nodes)
            ▼
  ┌─────────────────────────────────────────┐
  │           Execution Pipeline             │
  │                                          │
  │  Study Population Planner               │  ← Identifies study cohort
  │       ↓                                  │
  │  Segmentation Agent                     │  ← Runs nnUNet on studies
  │       ↓                                  │
  │  Feature Extraction Agent               │  ← PyRadiomics features
  │       ↓                                  │
  │  Modeling Agent                         │  ← PyCaret ML modeling
  │       ↓                                  │
  │  Result Reporter Agent                  │  ← Structures results
  │       ↓                                  │
  │  LaTeX Reporter Agent                   │  ← Generates PDF report
  └─────────────────────────────────────────┘
```

---

## Recommended Execution Order

1. **Hypothesis Architect** — Creates the research hypothesis
2. **Pipeline Architect** — Selects the pipeline steps
3. **Study Population Planner** — Identifies patient cohort
4. **Segmentation Agent** — Segments target structures
5. **Feature Extraction Agent** — Extracts radiomic features
6. **Modeling Agent** — Trains and evaluates ML models
7. **Result Reporter Agent** — Summarizes results
8. **LaTeX Reporter Agent** — Generates PDF report

---

## Agent Descriptions

### Hypothesis Architect
- **Input:** User's natural language research question
- **Output:** Structured hypothesis JSON with study goal, target population, modality, segmentation target, and expected outcome
- **Location:** `Backend/agent_system/agents/planning_agents/`

### Pipeline Architect
- **Input:** Hypothesis JSON
- **Output:** Ordered list of pipeline steps with agent names, descriptions, and matched modules
- **Canvas representation:** Generates the node graph displayed in the Research Playground

### Study Population Planner Agent
- **Input:** Hypothesis JSON, available studies from Orthanc
- **Output:** List of selected study UIDs with cohort rationale
- **Side effects:** Writes cohort to workspace scratchpad

### Segmentation Agent
- **Input:** Study UIDs, target structure name
- **Output:** Segmentation masks (NIfTI) per study
- **Mechanism:** Calls MONAI Label API → nnUNet inference

### Feature Extraction Agent
- **Input:** Study UIDs, segmentation masks
- **Output:** Radiomic features CSV (PyRadiomics)
- **Location:** Writes to `workspace/features.csv`

### Modeling Agent
- **Input:** Features CSV, target variable definition
- **Output:** Trained model, performance metrics (AUC, accuracy, etc.)
- **Mechanism:** PyCaret AutoML, scikit-learn

### Result Reporter Agent
- **Input:** Model results, features analysis
- **Output:** Structured results JSON with tables and figures
- **Side effects:** Saves figures to workspace

### LaTeX Reporter Agent
- **Input:** Structured results JSON
- **Output:** Compiled PDF report
- **Mechanism:** Generates `.tex` file, compiles with `pdflatex`
- **Endpoint:** `POST /compile_report`

---

## Workspace Structure

All agents share a workspace directory per research session:

```
agents_workspace/
└── {session_id}/
    ├── scratchpad.json          ← Shared cross-agent state
    ├── config.json              ← Session configuration (study UIDs, etc.)
    ├── targets.csv              ← Cohort labels for ML
    ├── features.csv             ← Radiomic feature matrix
    ├── outputs/
    │   ├── hypothesis_architect_output.json
    │   ├── pipeline_architect_output.json
    │   ├── study_population_planner_agent_output.json
    │   ├── segmentation_agent_output.json
    │   ├── feature_extraction_agent_output.json
    │   ├── modeling_agent_output.json
    │   ├── result_reporter_agent_output.json
    │   └── latex_reporter_agent_output.json
    └── reports/
        ├── report.tex
        └── report.pdf
```

---

## Agent I/O Conventions

- Structured outputs are written by `write_agent_output()` into `WORKSPACE_DIR/outputs/{agent_id}_output.json`
- Scratchpad data is shared in `WORKSPACE_DIR/scratchpad.json`
- Agent IDs **must stay consistent** across:
  - Team wiring
  - Scratchpad reads and writes
  - `write_agent_output` payloads

### ⚠️ Known Naming Mismatch
- `result_reporter_agent` is the runtime name used in the team
- Some prompts/scratchpad calls may use `result_agent`
- **Always use** `result_reporter_agent` as the canonical ID

---

## WebSocket Protocol

The Agent System exposes a native FastAPI WebSocket endpoint. Every connection
must be bound to one session with `/ws?session_id=<id>`. Server events are sent
only to sockets registered for that session; multiple tabs may intentionally
share the same session stream.

Each server event uses this envelope:

```json
{
  "version": 1,
  "event_id": "uuid",
  "sequence": 42,
  "session_id": "session-uuid",
  "run_id": null,
  "type": "pipeline_agent_status",
  "payload": {},
  "timestamp": "2026-08-21T12:00:00Z",
  "replaying": false
}
```

### Events (Server → Client)

| Event | Payload | Description |
|---|---|---|
| `plan` | `{steps: [...]}` | Pipeline plan ready |
| `pipeline_agent_status` | `{agents: [...]}` | Pipeline agent states |
| `state_update` | `{state}` | Session lifecycle state |
| `run_queued` | `{queue_position}` | Pipeline is waiting for capacity |
| `planning_queued` | `{queue_position}` | Planning is waiting for capacity |
| `msg` | string or object | Chat message from an agent |
| `protocol_error` | `{reason}` | Rejected client WebSocket message |

### Events (Client → Server)

| Event | Payload | Description |
|---|---|---|
| `user` | `{text}` or string | Queue user input for the bound session |
| `user_input` | `{text}` or string | Alias for `user` |
| `chat_input` | `{message}` or string | Alias for `user` |

Client messages should include a unique `message_id`; duplicate IDs are ignored
within the same session and acknowledged with `client_message_ack`. Pipeline
start, pause, resume, and stop commands use REST endpoints rather than WebSocket
control events.

Without Redis, routing and capacity queues are process-local. Run exactly one
Agent System worker. A later multi-worker deployment must add a shared broker and
distributed admission control before increasing the worker count.

---

## REST Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/sessions` | Create a new research session |
| `GET` | `/sessions/{id}` | Get session details |
| `POST` | `/compile_report` | Compile LaTeX → PDF |
| `GET` | `/reports/{session_id}` | Download PDF report |

---

## LLM Configuration

The Agent System uses **Ollama** with `deepseek-v3.1:671b-cloud` by default.

Configuration in `Backend/agent_system/client_config.py`:
- LLM client initialization
- Model selection
- Temperature and generation parameters

To use a different model, update the model name in `client_config.py` and ensure it is pulled in Ollama.

---

## Individual Agent I/O Contracts

Detailed input/output specifications for each agent are located in the `io-contracts/` directory.

---

## See Also

- [Research Playground](../research-playground/README.md) — Frontend canvas that visualizes this pipeline
- [API Reference](../api-reference/README.md) — Full API documentation
- [Architecture](../architecture/README.md) — System-level view
