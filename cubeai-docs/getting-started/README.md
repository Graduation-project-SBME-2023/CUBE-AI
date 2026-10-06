# Getting started

This guide covers a first local CUBE.AI setup. Use the guided launchers for the normal full stack; use the manual commands when actively developing source code.

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Docker and Docker Compose | Docker 24+ / Compose v2+ | All backend services and production frontend |
| Ollama | Current supported release | Default LLM provider for the Agent System |
| Node.js and Yarn Classic | Node 18+ / Yarn 1.22+ | Frontend development; production builds with Node 20 |
| Python | 3.13 | Local agent development, tests, and storage migration |
| Git | Current supported release | Source checkout |

GPU mode additionally requires an NVIDIA GPU, compatible drivers, and NVIDIA Container Toolkit. CPU mode works without CUDA but is slower for inference.

## 1. Configure Ollama

Install and start Ollama, then verify it responds:

```bash
ollama --version
ollama list
```

The default configured agent model is `gpt-oss:120b-cloud`. Cloud-hosted Ollama models may require login:

```bash
ollama login
ollama pull gpt-oss:120b-cloud
ollama run gpt-oss:120b-cloud
```

The Research Playground can also configure an OpenAI or Azure OpenAI-compatible provider through the Agent System client settings. Ollama is what the launchers check by default.

## 2. Clone and configure

```bash
git clone https://github.com/Graduation-project-SBME-2023/OHIF-Standalone.git
cd OHIF-Standalone
```

Create the local environment file from the tracked template:

```powershell
# Windows
Copy-Item deploy/.env.example deploy/.env
```

```bash
# Linux/macOS
cp deploy/.env.example deploy/.env
```

Review `deploy/.env` before startup. Change development passwords and provider settings as needed. Never commit the populated file or paste its secrets into logs or issues.

## 3. Recommended full-stack startup

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts/launch.ps1
```

The launcher prompts for CPU or GPU, sets `COMPOSE_PROJECT_DIR`, runs the safe legacy-storage migration, starts the backend and production frontend, waits for health, and follows logs. Open `http://localhost` when ready.

For backend only, using images already present locally:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/launch.backend.ps1
```

### Linux or macOS

```bash
bash scripts/launcher.linux.sh
```

This launcher prompts for CPU/GPU and Docker/development frontend modes. For backend only:

```bash
bash scripts/launcher.backend.linux.sh
```

## 4. Manual development startup

Set the absolute repository root because Compose uses it for bind mounts:

```powershell
$env:COMPOSE_PROJECT_DIR = (Get-Location).Path
```

```bash
export COMPOSE_PROJECT_DIR="$(pwd)"
```

Start one backend base plus the development override:

```powershell
# CPU
docker compose --env-file deploy/.env -f deploy/docker-compose.backend.cpu.yml -f deploy/docker-compose.backend.dev.yml up

# GPU
docker compose --env-file deploy/.env -f deploy/docker-compose.backend.gpu.yml -f deploy/docker-compose.backend.dev.yml up
```

In a second terminal, start the Webpack development server:

```bash
cd Frontend/OHIF-Viewer
yarn install
yarn run dev
```

Open `http://localhost:3000`.

## 5. Verify services

| Service | Development URL | Expected result |
|---|---|---|
| OHIF development server | `http://localhost:3000` | Worklist loads |
| Application API | `http://localhost:8001/health/ready` | `status: ready` plus an Alembic revision |
| Agent System | `http://localhost:8150/client-config` | Current client configuration JSON |
| MONAI Label | `http://localhost:8000/info/` | Server/model information JSON |
| Orthanc | `http://localhost:8043/system` | Orthanc system information JSON |
| PostgreSQL | `localhost:5433` | Accepts configured credentials |

In production, use `http://localhost`, `/api/health/ready`, `/agents/client-config`, `/inference/info/`, and `/pacs/system` through the same origin. PostgreSQL and backend ports can be hidden by the production backend overlay.

## 6. Load a DICOM study

1. Open Orthanc Explorer at `http://localhost:8043` in development.
2. Sign in with the credentials configured in `deploy/.env`.
3. Upload a DICOM file or archive.
4. Wait for the series to become stable. Orthanc notifies the Application API, which synchronizes metadata to PostgreSQL.
5. Refresh the OHIF worklist and open the study.

Do not use identifiable patient data in shared development or test environments without the required clinical-data controls.

## 7. Run a research pipeline

1. Open the Research Playground from the viewer or worklist.
2. Describe the population, modality, ROI, comparison, and outcome clearly.
3. Review the proposed hypothesis and pipeline.
4. Resolve cohort or segmentation review pauses.
5. Start execution and watch node/resource events.
6. Inspect feature/model outputs and download the report.

`WAITING_FOR_RESOURCE` means GPU or CPU-heavy work is queued. It is not a request for user input.

## Troubleshooting

| Problem | Check |
|---|---|
| Launcher cannot find `.env` | Copy `deploy/.env.example` to `deploy/.env` |
| Bind-mount path errors | Ensure `COMPOSE_PROJECT_DIR` is the absolute repository root; launchers set it automatically |
| Agent health takes a long time | Cold imports may take more than two minutes; Compose allows a 180-second start period |
| Agent model fails | Confirm Ollama is running, the model exists, and client settings use a reachable base URL |
| MONAI fails in GPU mode | Check drivers and Container Toolkit, then try the CPU compose file |
| Studies do not appear | Check Orthanc logs, the stable-series webhook, and Application API sync status |
| Frontend cannot reach a service | Distinguish direct development ports from production same-origin prefixes |
| Database is not ready | Inspect `db-migrate`, `db-seed`, `postgres`, then `application-api` logs |

Avoid `docker compose down -v` as a generic fix: `-v` removes named PostgreSQL and Orthanc volumes and can destroy local data. Use it only for a deliberate full reset with backups.

## Next steps

- [System overview](../system-overview.md)
- [Architecture](../architecture/README.md)
- [Deployment](../deployment/README.md)
- [Backend testing](../backend/testing.md)
- [Agent System](../agent-system/README.md)
