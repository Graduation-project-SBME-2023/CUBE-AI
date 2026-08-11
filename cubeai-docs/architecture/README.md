# 🏗️ System Architecture

## Overview

Architecture decision: [contention load test and service-split evaluation](./load-test-decision.md).

CUBE.AI is a modular, containerized platform that integrates multiple specialized services into a unified clinical research environment. All services communicate over a shared Docker network.

For a visual, browser-based reference, open [the interactive architecture page](./arch.html).

---

## High-Level Component Diagram

```
┌───────────────────────────────────────────────────────────────┐
│                        USER BROWSER                           │
│                                                               │
│   ┌────────────────────────────────────────────────────────┐  │
│   │              OHIF Viewer (React App)                   │  │
│   │                                                        │  │
│   │  ┌──────────────┐  ┌──────────────────────────────┐   │  │
│   │  │  Study List  │  │   Research Playground        │   │  │
│   │  │  (Worklist)  │  │   (Canvas + Chat Sidebar)    │   │  │
│   │  └──────────────┘  └──────────────────────────────┘   │  │
│   │                              │                          │  │
│   │                     WebSocket + REST                    │  │
│   └──────────────────────────────┼─────────────────────────┘  │
└──────────────────────────────────┼────────────────────────────┘
                                   │
           ┌───────────────────────┼────────────────────────┐
           │              BACKEND SERVICES                   │
           │                                                 │
           │  ┌──────────────────────────────────────────┐   │
           │  │       Agent System  (FastAPI)            │   │
           │  │   WebSocket · REST · Task Manager        │   │
           │  │   Multi-Agent LLM Pipeline               │   │
           │  └──────────┬───────────────────────────────┘   │
           │             │                                    │
           │  ┌──────────▼──────────┐  ┌────────────────┐   │
           │  │  MONAI Label        │  │   Orthanc      │   │
           │  │  (Segmentation AI)  │  │  (DICOM PACS)  │   │
           │  └──────────┬──────────┘  └────────┬───────┘   │
           │             │                       │            │
           │  ┌──────────▼───────────────────────▼───────┐   │
           │  │           PostgreSQL Database             │   │
           │  └──────────────────────────────────────────┘   │
           └─────────────────────────────────────────────────┘
                                   │
           ┌───────────────────────▼────────────────────────┐
           │                  Ollama (LLM Runtime)           │
           │           deepseek-v3.1:671b-cloud              │
           └─────────────────────────────────────────────────┘
```

---

## Services & Ports

| Service | Technology | Default Port | Role |
|---|---|---|---|
| **OHIF Viewer** | React + Vite | `3000` (dev) / `80` (prod) | Clinical imaging UI |
| **Agent System** | FastAPI + WebSocket | `8100` | LLM research pipeline backend |
| **MONAI Label** | FastAPI | `8000` | AI-assisted segmentation |
| **Orthanc** | C++ DICOM | `8042` (API) / `8043` (Web) | DICOM PACS server |
| **PostgreSQL** | PostgreSQL 15 | `5432` | Persistent database |
| **Ollama** | Ollama runtime | `11434` | Local/cloud LLM inference |

---

## Data Flow

### 1. DICOM Study Ingestion
```
External Source → Orthanc DICOM API (port 4242)
                → Orthanc stores in volume
                → Webhook fires → Agent System notified
                → PostgreSQL study record created
```

### 2. AI-Assisted Segmentation
```
User selects study in OHIF → MONAI Label Panel opens
→ Inference request to MONAI Label (/infer endpoint)
→ nnUNet model runs segmentation
→ Segmentation mask returned as DICOM-SEG / NIfTI
→ OHIF renders overlay
```

### 3. Automated Research Pipeline
```
User opens Research Playground → Chat sidebar initiates session
→ WebSocket connects to Agent System
→ User describes research question
→ Hypothesis Architect agent creates hypothesis
→ Pipeline Architect generates execution plan (nodes/edges)
→ Canvas renders interactive node graph
→ User approves → Pipeline executes:
    Study Population Planner → Segmentation → Feature Extraction
    → Modeling → Result Reporter → LaTeX Report Generator
→ PDF report generated & downloadable
```

---

## Technology Stack

| Layer | Technologies |
|---|---|
| **Frontend** | React 18, TypeScript, ReactFlow, Socket.IO Client, OHIF Extensions |
| **Backend API** | FastAPI, Python 3.11, SQLAlchemy, Alembic, asyncio |
| **AI/ML** | MONAI Label, nnUNetv2, PyRadiomics, PyCaret, scikit-learn |
| **LLM Integration** | Ollama, LiteLLM, deepseek-v3.1 |
| **Database** | PostgreSQL 15, asyncpg |
| **DICOM** | Orthanc, DICOMweb, dicom-web |
| **Infrastructure** | Docker, Docker Compose, Nginx |
| **Communication** | REST, WebSocket (Socket.IO), Orthanc Webhooks |

---

## Key Design Decisions

### Multi-Agent Architecture
Rather than a single monolithic AI model, CUBE.AI uses a **pipeline of specialized agents**, each responsible for one research step. This enables:
- Parallel execution of independent steps
- Transparent intermediate outputs (visible in the canvas)
- Easy extension by adding new agent types

### WebSocket-First for Research Pipeline
The Research Playground communicates exclusively via **WebSocket** to enable real-time streaming of:
- Agent status updates
- Execution logs per node
- Plan updates (dynamic re-planning)

### Shared Workspace Pattern
All agents share a **workspace directory** on disk:
```
agents_workspace/
  {session_id}/
    scratchpad.json       ← shared cross-agent state
    outputs/
      {agent_id}_output.json  ← structured agent output
    reports/              ← generated LaTeX/PDF files
```

---

## See Also

- [Deployment Guide](../deployment/README.md) — How to run all services
- [Agent System](../agent-system/README.md) — Deep-dive into the AI pipeline
- [API Reference](../api-reference/README.md) — All REST/WebSocket endpoints
