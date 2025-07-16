Contains info on how to run the application in both production and development modes.

## Prerequisites
- Ensure you have Docker and Docker Compose installed on your machine.
- Clone the repository to your local machine.

```bash
git clone https://github.com/Graduation-project-SBME-2023/OHIF-Standalone.git
cd OHIF-Standalone
git checkout Refactored-file-structure
```

- Install sam checkpoints
```bash
cd Backend/apps/radiology/sam2/checkpoints
bash download_ckpts.sh
```

- Set environment variable
-- On mac/linux:
```bash
export COMPOSE_PROJECT_DIR="$(pwd)"
```
-- On Windows:
```powershell
$env:COMPOSE_PROJECT_DIR = (Get-Location).Path
```

# 1. Running the Application in Production Mode
To run the application in production mode, you will need to set up the frontend and backend services using Docker. Below are the steps to get everything running smoothly.

## Running the docker containers
From the root, run the following command:

```bash
docker compose -f Frontend/OHIF-Viewer/docker-compose.frontend.prod.yml -f Backend/docker-compose.backend.cpu.yml -f Backend/docker-compose.backend.prod.yml up --build
```
This command will build and start the frontend and backend services in production mode.

Or if GPU is available, you can run:

```bash
docker compose -f Frontend/OHIF-Viewer/docker-compose.frontend.prod.yml -f Backend/docker-compose.backend.gpu.yml -f Backend/docker-compose.backend.prod.yml up --build
```

**The `--build` flag is needed for the very first time the frontend compose file is ran, for further trials, if no changes occured to the frontend, you can omit the flag to reduce start up time.**

## Accessing the Application
Once the services are up and running, you can access the application by navigating to `http://localhost`.

---

# 2. Running the Application in Development Mode

To run the application in dev mode with hot reload enabled for both the frontend and backend, follow the steps below:

## Run the Backend
From the root directory, run the following command:

```bash
docker compose -f Backend/docker-compose.backend.gpu.yml -f Backend/docker-compose.backend.dev.yml up
```

or if you want to run it on CPU:

```bash
docker compose -f Backend/docker-compose.backend.cpu.yml -f Backend/docker-compose.backend.dev.yml up
```

This will start the backend services (Orthanc, monailabel, postgresql) in development mode.

## Run the Frontend
From the root directory, run the following commands:

```bash
cd Frontend/OHIF-Viewer
yarn install
yarn run dev
```

This will run the react app on `http://localhost:3000` with hot reload enabled.
