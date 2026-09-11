# CUBE.AI Setup and Deployment Guide

Welcome to the CUBE.AI deployment guide. We've automated and simplified the deployment process so you can get the full platform (Frontend + AI Agents Backend) up and running with a single script.

> [!IMPORTANT]
> **Fresh Start Recommended:** If you have run older versions of CUBE.AI before, it is highly recommended to start fresh. Delete your old cloned repository, and clear out old Docker volumes from Docker Desktop to ensure a clean database state.

## 1. Prerequisites

Before you begin, ensure you have the following installed:
- **Git**
- **Docker Desktop** (Windows/macOS) or **Docker Engine with Compose** (Linux)
- **Ollama** (for connecting to the cloud LLM agent)
- *(Optional for GPU Mode)*: An NVIDIA CUDA-capable GPU and the NVIDIA Container Toolkit.

## 2. Configure Ollama

CUBE.AI relies on Ollama to connect to the cloud AI models. 
1. Install Ollama from [ollama.com/download](https://ollama.com/download).
2. Open a terminal and log in to access the cloud models:
   ```bash
   ollama login
   ```
3. Leave the Ollama app running in the background.

## 3. Clone the Repository

Clone the latest `Release-Version` branch:
```bash
git clone --branch Release-Version https://github.com/Graduation-project-SBME-2023/OHIF-Standalone.git
cd OHIF-Standalone
```

## 4. Setup the Environment File

To ensure the backend containers can communicate properly, you need `deploy/.env`. We have already included it directly in the repository for now since it is not public yet, so everything might work automatically!

However, we also provide `deploy/.env.example` as a fallback. If your environment file is missing, run this command inside the `OHIF-Standalone` directory:

**Windows PowerShell:**
```powershell
Copy-Item deploy/.env.example deploy/.env
```
**macOS/Linux:**
```bash
cp deploy/.env.example deploy/.env
```

## 5. Launch the Application

You are now ready to launch! You do not need to manually pull images or run complex docker commands. The interactive launcher script will automatically download any missing Docker images and start the entire stack.

**On Windows:**
Double-click `scripts/launcher.windows.bat`, or run it from the project root:
```powershell
.\scripts\launcher.windows.bat
```

**On Linux/macOS:**
```bash
bash ./scripts/launcher.linux.sh
```

### What to expect:
1. The script checks whether Docker and Ollama are installed. If either is missing, it lists every missing prerequisite with its official installation link and stops before attempting deployment.
2. If they are installed but inactive, the script starts Docker and Ollama and waits for both services to become ready. If automatic startup fails, it explains how to start them manually.
3. The script starts the GPU backend and automatically pulls the production images for both the Frontend and the Backend. *(Note: The first run may take several minutes as it downloads the images).*
4. Once everything says "started", open your browser and navigate to:
   **`http://localhost`**

## Troubleshooting

- **400 Bad Request / Chat stuck on Waiting:** Ensure you copied `deploy/.env.example` to `deploy/.env` as shown in step 4.
- **Docker errors:** Confirm Docker Desktop is open and says "Docker is running".
- **Ollama errors:** Ensure you ran `ollama login` successfully.
- **Stop the stack:** You can simply close the launcher window (or press `Ctrl+C` in the terminal) to stop the containers gracefully.
