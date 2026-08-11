# 🚀 Getting Started

This guide walks you through setting up CUBE.AI for the first time on your local machine.

---

## Prerequisites

Before you begin, make sure you have the following installed:

| Tool | Version | Notes |
|---|---|---|
| **Docker** | 24+ | Required for all backend services |
| **Docker Compose** | v2+ | Bundled with Docker Desktop |
| **Node.js** | 18+ | For frontend development |
| **Yarn** | 1.22+ | Frontend package manager |
| **Ollama** | Latest | LLM runtime (for the agent pipeline) |
| **Python** | 3.11.3 | Only needed for local agent dev |
| **Git** | Any | For cloning the repository |

---

## Step 1 — Install Ollama

The Agent System requires Ollama to run LLM inference locally or via cloud.

**macOS / Linux:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

**Windows:**  
Download the installer from [https://ollama.com/download](https://ollama.com/download) and follow the prompts.

**Verify installation:**
```bash
ollama --version
```

**Pull the required model:**
```bash
# Log in (needed for cloud models)
ollama login

# Pull the model used by the agent pipeline
ollama pull deepseek-v3.1:671b-cloud

# Verify it responds
ollama run deepseek-v3.1:671b-cloud
# Type a test message, then exit with /bye
```

---

## Step 2 — Clone the Repository

```bash
git clone https://github.com/Graduation-project-SBME-2023/OHIF-Standalone.git
cd OHIF-Standalone
git checkout demoV2.0
```

---

## Step 3 — Configure Environment Variables

The Docker Compose files use `COMPOSE_PROJECT_DIR` to resolve volume paths.

**macOS / Linux:**
```bash
export COMPOSE_PROJECT_DIR="$(pwd)"
```

**Windows (PowerShell):**
```powershell
$env:COMPOSE_PROJECT_DIR = (Get-Location).Path
```

> 💡 Add this to your shell profile (`~/.bashrc`, `~/.zshrc`, or PowerShell profile) so it persists across sessions.

---

## Step 4 — Start the Backend

**With GPU (recommended):**
```bash
docker compose --env-file deploy/.env \
               -f deploy/docker-compose.backend.gpu.yml \
               -f deploy/docker-compose.backend.dev.yml up
```

**CPU only:**
```bash
docker compose --env-file deploy/.env \
               -f deploy/docker-compose.backend.cpu.yml \
               -f deploy/docker-compose.backend.dev.yml up
```

This will start:
- 🗃️ **Orthanc** — DICOM server at `http://localhost:8043`
- **Application API** — CRUD, chat, workflow, and sync at `http://localhost:8001`
- 🤖 **MONAI Inference** — Segmentation and training at `http://localhost:8000`
- 🗄️ **PostgreSQL** — Database at `localhost:5433`
- 🧠 **Agent System** — Research pipeline at `http://localhost:8150`

Wait until you see all services report `ready` or similar startup messages.

---

## Step 5 — Start the Frontend

Open a new terminal window:

```bash
cd Frontend/OHIF-Viewer
yarn install
yarn run dev
```

The frontend will be available at **[http://localhost:3000](http://localhost:3000)**.

---

## Step 6 — Verify Everything is Running

| Service | URL | Expected Response |
|---|---|---|
| OHIF Viewer | http://localhost:3000 | Study list page loads |
| Orthanc Web UI | http://localhost:8043 | Orthanc dashboard |
| Application API | http://localhost:8001/health/ready | `ready` status after migrations |
| MONAI Inference API | http://localhost:8000/info/ | JSON with server info |
| Agent System | http://localhost:8150/health | `{"status": "ok"}` |

---

## Loading Your First DICOM Study

1. Open the **Orthanc Web UI** at `http://localhost:8043`
2. Log in with credentials: `orthanc` / `orthanc` (default)
3. Click **Upload** and select a DICOM file or ZIP of DICOM series
4. Navigate to `http://localhost:3000` — your study should appear in the worklist

---

## Running Your First Research Pipeline

1. Open the worklist at `http://localhost:3000`
2. Select a study that has been successfully uploaded
3. Click **Open in Research Playground** (or navigate to the playground from the study viewer)
4. In the **Chat Sidebar**, describe your research question:
   > *"Analyze the correlation between tumor volume and patient age in this cohort."*
5. Watch the **Canvas** populate with agent nodes as the pipeline is planned
6. Review the plan, then click **Execute**
7. Monitor agent progress in the node graph and view the final PDF report

---

## Common First-Run Issues

| Problem | Solution |
|---|---|
| Docker containers fail to start | Run `docker compose down -v` then retry |
| Frontend can't connect to backend | Ensure `COMPOSE_PROJECT_DIR` is set correctly |
| Ollama model not found | Run `ollama pull deepseek-v3.1:671b-cloud` |
| MONAI Label crashes on startup | Check GPU drivers; use CPU compose file instead |
| No studies appear in OHIF | Upload via Orthanc UI first |

---

## Next Steps

- 📖 [Architecture Overview](../architecture/README.md) — Understand how everything connects
- ⚙️ [Deployment Guide](../deployment/README.md) — Production setup
- 🤖 [Agent System](../agent-system/README.md) — Deep dive into the AI pipeline
