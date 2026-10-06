# 🔧 Backend Documentation

The backend is composed of five main services, each running in its own Docker container.

---

## Services Overview

| Service | Role | Source |
|---|---|---|
| [Agent System](../agent-system/README.md) | Multi-agent LLM research pipeline | `Backend/agent_system/` |
| Application API | PostgreSQL CRUD, chat, workflow metadata, and Orthanc synchronization | `Backend/application_api/` |
| MONAI Label | AI-assisted segmentation server + nnUNet | `Backend/monailabel/` |
| Orthanc | DICOM PACS server | Docker image + config |
| [PostgreSQL](../database/README.md) | Persistent database | Docker image; schema owned by `Backend/application_api/` |

---

## Agent System

The core backend for the Research Playground. Built with **FastAPI** and **Python 3.13**.

**Entry point:** `Backend/agent_system/app.py`

### Key Responsibilities
- Manages WebSocket connections from the OHIF frontend
- Orchestrates the multi-agent research pipeline
- Exposes REST endpoints for session management and report compilation
- Communicates with Ollama for LLM inference
- Reads/writes the shared agent workspace directory

### Configuration

```bash
# Run locally (outside Docker)
python ./Backend/agent_system/app.py

# Inside Docker (automatic)
# See deploy/Dockerfile.agents
```

### Dependencies

Install with:
```bash
cd Backend/agent_system
pip install -r requirements.txt
pip install git+https://github.com/AIM-Harvard/pyradiomics.git@8ed5793
```

> PyCaret is no longer installed; there are no late numeric pins to install last.

---

## MONAI Label

MONAI Label provides the AI-assisted segmentation capabilities. It runs as a standalone server using the **Radiology app** configuration.

It does not import SQLAlchemy models, receive `DATABASE_URL`, or apply database migrations. Database-backed operations are exposed by the Application API on port `8001`.

**Port:** `8000`

### Starting the Server (manual / outside Docker)
```bash
monailabel start_server --app radiology --studies http://localhost:8042/dicom-web --conf models all
```

### Custom Extensions (Generic Framework)

The project extends MONAI Label with a **Generic Segmentation Framework** that allows dynamic model creation without restarting the server. See [Generic Framework Guide](./generic_framework_guide.md) for the full implementation.

Key added endpoints:
- `POST /monailabel/create-model` — Dynamically registers a new nnUNet model
- `POST /monailabel/initialize-inference-task` — Activates inference for a newly trained model

### SAM2 Integration

MONAI Label also integrates **Segment Anything Model v2** for interactive segmentation.

Required checkpoint: Download from the [SAM2 Checkpoints (Google Drive)](https://drive.google.com/drive/folders/15KWC6C-Wnk6EwWLBHdCi2RiMFnphFhw4) and place in:
```
Backend/apps/radiology/sam2/checkpoints/
```

---

## Orthanc

Orthanc is the DICOM PACS server that stores and serves medical imaging data.

**Ports:**
- `8042` (container) / `8043` (published to the host) — REST API, DICOMweb, and the built-in web UI (all on the same port)
- `4242` — DICOM protocol (C-STORE, C-FIND, etc.) — not published to the host in any compose file

**Authentication:** disabled by default (`AuthenticationEnabled: false` in
`Backend/orthanc/orthanc.json`). The `RegisteredUsers` entry in that file (`test`/`test`) is inert
while auth is disabled and does **not** match the `orthanc`/`orthanc` credentials the application
services send — enabling auth requires reconciling both. See
`cubeai-docs/backend/database_audit.md` findings D-30 and P-1/P-3 before relying on this outside the
docker-compose network; the agent system and MONAI Label currently send no Orthanc credentials at
all and must be updated in the same change that enables auth.

### Useful Commands

```bash
# Upload a DICOM file (credentials below are accepted but not required while auth is disabled)
curl -u orthanc:orthanc -X POST http://localhost:8042/instances \
     -H "Content-Type: application/dicom" \
     --data-binary @path/to/file.dcm

# List all studies
curl -u orthanc:orthanc http://localhost:8042/studies

# DICOMweb WADO-RS endpoint
http://localhost:8042/dicom-web/studies
```

---

## PostgreSQL

PostgreSQL stores all structured application data including:
- Research sessions
- Agent pipeline state
- Study metadata
- Workflow annotations

**Managed via:** Alembic migrations  
**ORM:** SQLAlchemy (async)

The Application API exclusively owns the ORM models, repositories, seeders, and Alembic migration history under `Backend/application_api/`.

See the full [Database Documentation](../database/README.md).

---

## Data Transfer: PACS → Workflow

The system supports bulk data transfer from Orthanc into the MONAI Label workspace:

- **`POST /datastore/images`** — Download multiple DICOM series as NIfTI into `General_Data/`
- **`POST /datastore/labels`** — Download annotation labels into `Labels/`

Both endpoints use an `instance_mapping.json` file to avoid re-downloading already-processed data.

See [DataStore Guide](./datastore_api.md) for full API details.

---

## See Also

- [Backend Testing](./testing.md) — Test suites, markers, and local/CI commands
- [Generic Framework Guide](./generic_framework_guide.md) — Dynamic model creation in MONAI Label
- [NnUNet Integration](./handle_nnunet.md) — Training and inference with nnUNetv2
- [SAM2 Setup](./sam2_installation_guide.md) — Segment Anything Model 2 integration
- [Normalization & Orientation](./normalization_and_orientation.md) — Image preprocessing conventions
