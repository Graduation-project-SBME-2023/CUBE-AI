# 🧠 CUBE.AI — Centralized Documentation Hub

> **CUBE.AI** is a full-stack, AI-powered clinical research platform built on top of OHIF Viewer, Orthanc DICOM server, MONAI Label, and a multi-agent research pipeline backed by LLMs. This folder is the **single source of truth** for all project documentation.

---

## 📚 Documentation Sections

| Section | Description |
|---|---|
| [🏗️ Architecture](./architecture/README.md) | System overview, component diagram, and data flow |
| [🚀 Getting Started](./getting-started/README.md) | Quick start, prerequisites, and first-run guide |
| [⚙️ Deployment](./deployment/README.md) | Dev & production deployment with Docker |
| [🔧 Backend](./backend/README.md) | Backend services: MONAI Label, Orthanc, PostgreSQL, Agent System |
| [🎨 Frontend](./frontend/README.md) | OHIF Viewer extensions, Research Playground, UI components |
| [🤖 Agent System](./agent-system/README.md) | Multi-agent AI pipeline, agent I/O contracts, agent descriptions |
| [🗄️ Database](./database/README.md) | PostgreSQL schema, Alembic migrations, data models |
| [📡 API Reference](./api-reference/README.md) | REST API endpoints for all backend services |
| [🔬 Research Playground](./research-playground/README.md) | Canvas-based research pipeline UI and WebSocket protocol |
| [📝 Changelog & Refactoring](./changelog/README.md) | Notable refactors, version notes, and code evolution |

---

## 🗺️ Project At a Glance

```
CUBE.AI Platform
│
├── Frontend  (React / OHIF Viewer)
│   ├── Research Playground  ← AI pipeline canvas + chat
│   ├── OHIF Viewer          ← DICOM visualization
│   └── Workflow UI          ← Annotation & training workflows
│
├── Backend
│   ├── Agent System         ← Multi-agent LLM research pipeline (FastAPI + WebSocket)
│   ├── MONAI Label          ← AI-assisted segmentation server
│   ├── Orthanc              ← DICOM PACS server
│   └── PostgreSQL           ← Persistent data store (Alembic migrations)
│
└── Infrastructure
    ├── Docker Compose       ← Full stack orchestration
    └── Ollama               ← Local LLM runtime
```

---

## ⚡ Quick Start

**1. Clone the repository**
```bash
git clone https://github.com/Graduation-project-SBME-2023/OHIF-Standalone.git
cd OHIF-Standalone
git checkout demoV2.0
```

**2. Set the required environment variable**
```bash
# macOS / Linux
export COMPOSE_PROJECT_DIR="$(pwd)"

# Windows (PowerShell)
$env:COMPOSE_PROJECT_DIR = (Get-Location).Path
```

**3. Start the backend**
```bash
docker compose --env-file Backend/.env -f deploy/docker-compose.backend.gpu.yml -f deploy/docker-compose.backend.dev.yml up
```

**4. Start the frontend**
```bash
cd Frontend/OHIF-Viewer
yarn install && yarn run dev
# App available at http://localhost:3000
```

> For full deployment options see [Deployment Guide](./deployment/README.md).

---

## 🏆 Team & Project Context

This platform was built as a **Graduation Project** by the **SBME 2023 cohort** at Cairo University's Systems & Biomedical Engineering department. It provides clinicians and researchers with an integrated environment for:

- 📂 **DICOM management** via Orthanc
- 🔬 **AI-assisted segmentation** via MONAI Label + nnUNet
- 🧪 **Automated radiomics research** via the multi-agent pipeline
- 📊 **LaTeX report generation** from study results

---

*Last updated: June 2026*
