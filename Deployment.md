This guide explains how to run the OHIF-Standalone application in two modes:

- **[Production Mode](#2-running-the-application-in-production-mode)**: Optimized for deployment, with prebuilt assets and no hot-reloading. Ideal for stable environments and testing the final user experience.
- **[Development Mode](#1-running-the-application-in-development-mode)**: Enables hot-reload and live updates for both frontend and backend, suitable for local development and debugging.

Read below for detailed instructions on each.

## Prerequisites
- Ensure you have Docker and Docker Compose installed on your machine.

- **Download and install the Ollama client.**
  - Visit [https://ollama.com/download](https://ollama.com/download) and download the installer for your operating system (macOS, Windows, or Linux).
  - Follow the installation instructions for your platform:
    - **macOS/Windows**: Run the downloaded installer and follow the on-screen prompts.
    - **Linux**:
```bash
      curl -fsSL https://ollama.com/install.sh | sh
```
  - Once installed, verify Ollama is running:
```bash
    ollama --version
```
  - **Sign in to Ollama** (required for cloud models):
```bash
    ollama login
```
    Enter your Ollama account credentials when prompted. If you don't have an account, register at [https://ollama.com](https://ollama.com).
  - **Pull and run the `deepseek-v3.1:671b-cloud` model** to verify your setup and send your first message:
```bash
    ollama run deepseek-v3.1:671b-cloud
```
    This will download the model (first run only) and open an interactive chat session. Type a message and press **Enter** to confirm the model is working, then exit with `/bye` or `Ctrl+D`.

- Clone the repository to your local machine.

```bash
git clone https://github.com/Graduation-project-SBME-2023/OHIF-Standalone.git
cd OHIF-Standalone
git checkout demo
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

# 1. Running the Application in Development Mode

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

This will download the necessary docker images, then start the backend services (Orthanc, monailabel, postgresql) in development mode.

## Run the Frontend
From the root directory, run the following commands:

```bash
cd Frontend/OHIF-Viewer
yarn install
yarn run dev
```

This will run the react app on `http://localhost:3000` with hot reload enabled.

# 2. Running the Application in Production Mode
To run the application in production mode, you will need to set up the frontend and backend services using Docker. Below are the steps to get everything running smoothly.

## Running the docker containers
From the root, run the following command:

```bash
docker compose -f Frontend/OHIF-Viewer/docker-compose.frontend.prod.yml -f Backend/docker-compose.backend.cpu.yml up --build
```
This command will build and start the frontend and backend services in production mode.

Or if GPU is available, you can run:

```bash
docker compose -f Frontend/OHIF-Viewer/docker-compose.frontend.prod.yml -f Backend/docker-compose.backend.gpu.yml up --build
```

**The `--build` flag is needed for the very first time the frontend compose file is ran, for further trials, if no changes occured to the frontend, you can omit the flag to reduce start up time.**

## Accessing the Application
Once the services are up and running, you can access the application by navigating to `http://localhost`.

---
