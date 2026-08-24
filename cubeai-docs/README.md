# CUBE.AI documentation

CUBE.AI is a medical-imaging research platform built around a customized OHIF Viewer. It integrates Orthanc DICOM storage, PostgreSQL application metadata, MONAI Label inference and training, and an LLM-driven multi-agent radiomics pipeline.

This directory is the human-facing source of truth for the project. Start with the system overview, then follow the subsystem guide relevant to your work.

## Documentation map

| Section | Use it for |
|---|---|
| [System overview](./system-overview.md) | Product capabilities, repository map, service ownership, runtime flows, storage, and verification |
| [Architecture](./architecture/README.md) | Deployment topology, ports, ingress, data boundaries, and architectural decisions |
| [Getting started](./getting-started/README.md) | First-time configuration and local startup |
| [Deployment](./deployment/README.md) | Development and production Compose combinations |
| [Backend](./backend/README.md) | Application API, agent system, MONAI/radiology, Orthanc, and PostgreSQL |
| [Frontend](./frontend/README.md) | OHIF customizations, worklist/workflows, assisted segmentation, and Research Playground |
| [Agent system](./agent-system/README.md) | Planning/execution pipeline, session isolation, scheduling, and agent contracts |
| [Database](./database/README.md) | SQLAlchemy models, repositories, Alembic migrations, and seed data |
| [API reference](./api-reference/README.md) | REST and WebSocket contracts |
| [Research Playground](./research-playground/README.md) | User workflow, canvas phases, and live event handling |
| [Backend testing](./backend/testing.md) | Test suites, markers, dependencies, and CI behavior |
| [Changelog and refactoring](./changelog/README.md) | Significant design changes and migration notes |

Codex-specific repository context is kept in [`.codex/`](../.codex/README.md) and is discovered through [`AGENTS.md`](../AGENTS.md).

## System at a glance

```text
OHIF browser client
    |
    +-- Application API ---- PostgreSQL metadata
    +-- Agent System ------- LLM planning and research execution
    +-- MONAI Label -------- segmentation, data preparation, and training
    +-- Orthanc ------------ DICOM and DICOMweb
```

The processes share a product, not ownership:

- The **Application API** is the only service that directly accesses PostgreSQL.
- **MONAI Label** owns inference and training but has no database credentials.
- The **Agent System** orchestrates session-scoped research pipelines and calls the other services over HTTP.
- **Orthanc** owns DICOM objects; PostgreSQL stores searchable application metadata about them.
- **OpenResty** is the production ingress and maps `/api`, `/agents`, `/inference`, `/pacs`, and `/ws` to internal services.
- Generated files live under `Backend/storage/`; source code must resolve them through `Backend/storage_paths.py`.

## Quick start

1. Copy `deploy/.env.example` to `deploy/.env` and review the values.
2. Start Docker Desktop and Ollama.
3. Use the repository launcher:

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts/launch.ps1
```

```bash
# Linux/macOS
bash scripts/launcher.linux.sh
```

The launcher sets `COMPOSE_PROJECT_DIR`, checks dependencies, lets you choose CPU or GPU, migrates legacy storage safely, starts the stack, and waits for health checks. The production UI is available at `http://localhost`.

For a source-mounted backend and frontend development server, follow [Getting started](./getting-started/README.md).

## Project context

This platform was built as a graduation project by the SBME 2023 cohort at Cairo University's Systems and Biomedical Engineering department. Its major clinical-research capabilities are:

- DICOM management and viewing through Orthanc and OHIF.
- Assisted and automated segmentation through MONAI Label, nnUNet, VISTA, and interactive tools.
- Cohort construction, radiomics feature extraction, machine-learning modeling, and report generation through the agent pipeline.
- Annotation workflows and training-job management from the viewer.

Last verified against the repository: August 2026.
