# CUBE.AI Deployment Guide (`Release-Version`)

## Choose your setup

- [Part 1: First-time installation](#part-1-first-time-installation)
- [Part 2: Existing installation](#part-2-existing-installation)

---

<a id="part-1-first-time-installation"></a>

# Part 1: First-time installation

Use this part only when installing CUBE.AI for the first time.

## 1. Prerequisites

- Git
- Docker Desktop (Windows/macOS) or Docker Engine with the Compose plugin (Linux)
- Node.js and Yarn for frontend development
- Ollama for the agent-system language model
- For GPU mode: an NVIDIA CUDA-capable GPU and the NVIDIA Container Toolkit

## 2. Clone the `Release-Version` branch

```bash
git clone --branch Release-Version https://github.com/Graduation-project-SBME-2023/OHIF-Standalone.git
cd OHIF-Standalone
```

Run all remaining commands from the repository root unless a step says otherwise.

## 3. Configure the environment

Create the backend environment file:

```bash
# macOS/Linux
cp Backend/.env.example Backend/.env
```

```powershell
# Windows PowerShell
Copy-Item Backend/.env.example Backend/.env
```

Review `Backend/.env` and change credentials or model settings as needed. The branch defaults to the Ollama model `gpt-oss:120b-cloud`.

Set `COMPOSE_PROJECT_DIR` to the absolute repository path:

```bash
# macOS/Linux
export COMPOSE_PROJECT_DIR="$(pwd)"
```

```powershell
# Windows PowerShell
$env:COMPOSE_PROJECT_DIR = (Get-Location).Path
```

Set this variable again whenever you open a new terminal. Alternatively, set `COMPOSE_PROJECT_DIR` in `Backend/.env` to the absolute repository path.

## 4. Configure Ollama

Install Ollama from [ollama.com/download](https://ollama.com/download), then verify it:

```bash
ollama --version
```

Sign in and start the default cloud model once to verify access:

```bash
ollama login
ollama run gpt-oss:120b-cloud
```

Exit the interactive session with `/bye`. The Docker services connect to Ollama through `host.docker.internal:11434`.

## 5. Run in development mode

Development mode runs the backend in Docker and the frontend locally with hot reload.

### Start the backend

CPU:

```bash
docker compose --env-file Backend/.env -f Backend/docker-compose.backend.cpu.yml -f Backend/docker-compose.backend.dev.yml up
```

GPU:

```bash
docker compose --env-file Backend/.env -f Backend/docker-compose.backend.gpu.yml -f Backend/docker-compose.backend.dev.yml up
```

### Start the frontend

In a second terminal:

```bash
cd Frontend/OHIF-Viewer
yarn install
yarn run dev
```

Open `http://localhost:3000`.

## 6. Run in production mode

Production mode runs the complete stack in Docker.

CPU:

```bash
docker compose --env-file Backend/.env -f Backend/docker-compose.backend.cpu.yml -f Frontend/OHIF-Viewer/docker-compose.frontend.prod.yml up
```

GPU:

```bash
docker compose --env-file Backend/.env -f Backend/docker-compose.backend.gpu.yml -f Frontend/OHIF-Viewer/docker-compose.frontend.prod.yml up
```

Open `http://localhost` after the services become healthy.

## Service endpoints

| Service | Address |
| --- | --- |
| OHIF Viewer (production) | `http://localhost` |
| OHIF Viewer (development) | `http://localhost:3000` |
| Agent System API | `http://localhost:8150` |
| MONAI Label API | `http://localhost:8000` |
| Orthanc Explorer and DICOMweb/API | `http://localhost:8043` |
| PostgreSQL | `localhost:5433` |

## Stop the stack

Use the same compose files used to start the stack, replacing `up` with `down`. For example, to stop the CPU production stack:

```bash
docker compose --env-file Backend/.env -f Backend/docker-compose.backend.cpu.yml -f Frontend/OHIF-Viewer/docker-compose.frontend.prod.yml down
```

Add `-v` only when you intentionally want to remove the database and other named-volume data.

## Troubleshooting

- Confirm Docker is running with `docker compose version`.
- Confirm Ollama is reachable with `ollama list`.
- If a bind mount fails, verify that `COMPOSE_PROJECT_DIR` is the absolute path to this repository.
- Inspect services with `docker compose ... ps` and logs with `docker compose ... logs -f <service>`.
- The first MONAI Label startup can take longer while the Qwen embedding model is downloaded and validated.

---

<a id="part-2-existing-installation"></a>

# Part 2: Existing installation

Use this part when CUBE.AI is already installed and you want to run the project again. Do not clone the repository again.

## 1. Update the branch

Open a terminal in the existing `OHIF-Standalone` directory:

```bash
git switch Release-Version
git pull --ff-only origin Release-Version
```

If Git reports that local changes would be overwritten, commit or stash those changes before pulling.

## 2. Set the project directory

```bash
# macOS/Linux
export COMPOSE_PROJECT_DIR="$(pwd)"
```

```powershell
# Windows PowerShell
$env:COMPOSE_PROJECT_DIR = (Get-Location).Path
```

## 3. Pull the Docker images

Choose the command that matches your system.

CPU:

```bash
docker compose --env-file Backend/.env -f Backend/docker-compose.backend.cpu.yml -f Frontend/OHIF-Viewer/docker-compose.frontend.prod.yml pull
```

GPU:

```bash
docker compose --env-file Backend/.env -f Backend/docker-compose.backend.gpu.yml -f Frontend/OHIF-Viewer/docker-compose.frontend.prod.yml pull
```

Docker downloads only missing or changed image layers.

## 4. Start the updated application

CPU:

```bash
docker compose --env-file Backend/.env -f Backend/docker-compose.backend.cpu.yml -f Frontend/OHIF-Viewer/docker-compose.frontend.prod.yml up
```

GPU:

```bash
docker compose --env-file Backend/.env -f Backend/docker-compose.backend.gpu.yml -f Frontend/OHIF-Viewer/docker-compose.frontend.prod.yml up
```

Open `http://localhost` after the services become healthy.

[Back to setup choices](#choose-your-setup)
