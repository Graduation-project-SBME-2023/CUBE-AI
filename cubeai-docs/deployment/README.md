# ⚙️ Deployment Guide

This guide explains how to run CUBE.AI in **Development Mode** (with hot-reload) and **Production Mode** (containerized, optimized).

---

## Prerequisites

- Docker & Docker Compose installed
- Ollama installed and the `deepseek-v3.1:671b-cloud` model pulled
- `COMPOSE_PROJECT_DIR` environment variable set to the repo root:

```bash
# macOS / Linux
export COMPOSE_PROJECT_DIR="$(pwd)"

# Windows PowerShell
$env:COMPOSE_PROJECT_DIR = (Get-Location).Path
```

---

## Mode 1 — Development Mode

Development mode provides hot-reload for both frontend and backend. Ideal for active development.

### Start the Backend

**GPU:**
```bash
docker compose -f Backend/docker-compose.backend.gpu.yml \
               -f Backend/docker-compose.backend.dev.yml up
```

**CPU:**
```bash
docker compose -f Backend/docker-compose.backend.cpu.yml \
               -f Backend/docker-compose.backend.dev.yml up
```

The dev overlay (`docker-compose.backend.dev.yml`) mounts the source code as a volume, so changes to Python files trigger automatic restarts.

### Start the Frontend

```bash
cd Frontend/OHIF-Viewer
yarn install
yarn run dev
```

The React app runs at **http://localhost:3000** with Vite's HMR enabled.

---

## Mode 2 — Production Mode

Production mode builds the frontend into optimized static assets served by Nginx. No hot-reload.

### Single Command Startup

**CPU:**
```bash
docker compose \
  -f Frontend/OHIF-Viewer/docker-compose.frontend.prod.yml \
  -f Backend/docker-compose.backend.cpu.yml \
  up --build
```

**GPU:**
```bash
docker compose \
  -f Frontend/OHIF-Viewer/docker-compose.frontend.prod.yml \
  -f Backend/docker-compose.backend.gpu.yml \
  up --build
```

> ⚠️ The `--build` flag is only needed the **first time** or after frontend code changes. Omit it on subsequent runs to save startup time.

### Accessing the Application

Once all services are up, navigate to **http://localhost**.

---

## Service Endpoints (Both Modes)

| Service | URL |
|---|---|
| OHIF Viewer | `http://localhost` (prod) / `http://localhost:3000` (dev) |
| Agent System API | `http://localhost:8100` |
| MONAI Label API | `http://localhost:8000` |
| Orthanc Web UI | `http://localhost:8043` |
| Orthanc DICOM API | `http://localhost:8042` |
| PostgreSQL | `localhost:5432` |

---

## Managing Services

### Stop all containers
```bash
docker compose down
```

### Stop and remove volumes (full reset)
```bash
docker compose down -v
```

### View logs for a specific service
```bash
docker compose logs -f monailabel
docker compose logs -f agent-system
docker compose logs -f orthanc
```

### Restart a specific service
```bash
docker compose restart agent-system
```

---

## Environment Variables

The backend services are configured via `Backend/.env`. Key variables:

| Variable | Description | Default |
|---|---|---|
| `POSTGRES_DB` | Database name | `workflow_db` |
| `POSTGRES_USER` | DB username | `workflow_user` |
| `POSTGRES_PASSWORD` | DB password | *(set in .env)* |
| `ORTHANC_URL` | Orthanc API URL | `http://orthanc:8042` |
| `OLLAMA_HOST` | Ollama endpoint | `http://host.docker.internal:11434` |
| `AGENT_WORKSPACE_DIR` | Path for agent outputs | `/app/agents_workspace` |

---

## All-in-One Launcher Scripts

For convenience, launcher scripts are available in the project root:

| Script | OS | Description |
|---|---|---|
| `launch.ps1` | Windows | PowerShell launcher |
| `launcher.linux.sh` | Linux/macOS | Bash launcher |
| `launcher.windows.bat` | Windows | Batch launcher |

---

## GPU Requirements

- NVIDIA GPU with CUDA support (for GPU compose files)
- NVIDIA Container Toolkit installed: [Installation Guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
- Minimum: 8GB VRAM (recommended: 16GB+ for large models)

---

## See Also

- [Getting Started](../getting-started/README.md) — First-time setup
- [Backend](../backend/README.md) — Service configuration details
- [Database](../database/README.md) — Schema and migrations
