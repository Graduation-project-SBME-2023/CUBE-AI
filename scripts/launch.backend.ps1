#  =========================================
#  OHIF-Standalone Backend-only launcher
#  Double-click this file to launch
#
#  - No frontend: use launch.ps1 for both.
#  - No image updates: runs whatever images are already on this
#    machine (--pull never). Use launch.ps1 when you actually
#    want to fetch newer images.
#  - Applies the storage changes: compose recreates only the
#    containers whose spec changed, which is what makes the new
#    Backend\storage mounts and BACKEND_STORAGE_ROOT / *_DIR env
#    vars take effect.
#  =========================================

# Keep the window open if something goes wrong
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-OK {
    param($m)
    Write-Host "[OK] $m" -ForegroundColor Green
}

function Write-Warn {
    param($m)
    Write-Host "[!] $m" -ForegroundColor Yellow
}

function Stop-Launch {
    param($m)
    Write-Host "[ERROR] $m" -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host ""
Write-Host "  ========================================="
Write-Host "  OHIF-Standalone Backend-only launcher"
Write-Host "  ========================================="
Write-Host ""

# -- 1. Docker --------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-Launch "Docker is not installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ then re-run this script."
}

try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -ne 0) { throw }
}
catch {
    Stop-Launch "Docker Desktop is not running.`nPlease open Docker Desktop, wait until it shows 'Docker is running', then double-click this script again."
}

Write-OK "Docker is running."

#  = 2. Ollama ==================================
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Stop-Launch "Ollama is not installed. Download it from https://ollama.com/download then re-run this script."
}

$ollamaOk = $false

try {
    $resp = Invoke-WebRequest `
        -Uri "http://localhost:11434" `
        -UseBasicParsing `
        -TimeoutSec 3 `
        -ErrorAction SilentlyContinue

    if ($resp.StatusCode -lt 500) {
        $ollamaOk = $true
    }
}
catch {}

if (-not $ollamaOk) {
    try {
        $null = ollama list 2>&1
        if ($LASTEXITCODE -eq 0) {
            $ollamaOk = $true
        }
    }
    catch {}
}

if (-not $ollamaOk) {
    Stop-Launch "Ollama does not appear to be running.`nOpen the Ollama app from your Start menu, wait a moment, then double-click this script again."
}

Write-OK "Ollama is running."

#  === 3. Project paths =======================================
$ScriptDir = $PSScriptRoot
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$BackendEnv = Join-Path $ProjectRoot "Backend\.env"
$env:COMPOSE_PROJECT_DIR = $ProjectRoot

if (-not (Test-Path -LiteralPath $BackendEnv -PathType Leaf)) {
    Stop-Launch "Backend environment file not found: $BackendEnv`nCopy Backend\.env.example to Backend\.env and try again."
}

Write-OK "COMPOSE_PROJECT_DIR set to: $ProjectRoot"

#  === 4. Backend Selection =================================
Write-Host ""
Write-Host "Select backend mode:"
Write-Host "  1) GPU"
Write-Host "  2) CPU"
Write-Host ""

:backendLoop while ($true) {
    $BackendChoice = Read-Host "Choice [1-2]"

    switch ($BackendChoice) {
        "1" {
            $BackendCompose = Join-Path $ProjectRoot "deploy\docker-compose.backend.gpu.yml"
            $BackendMode = "GPU"
            break backendLoop
        }

        "2" {
            $BackendCompose = Join-Path $ProjectRoot "deploy\docker-compose.backend.cpu.yml"
            $BackendMode = "CPU"
            break backendLoop
        }

        default {
            Write-Warn "Invalid selection."
        }
    }
}

Write-Host ""
Write-OK "Selected backend: $BackendMode"
Write-Warn "Frontend will NOT be started. Run scripts\launch.ps1 if you need the viewer."
Write-Host ""

#  === 5. Storage migration =================================
# Relocates pre-restructure runtime data into Backend/storage. Silent no-op
# once migrated, and on a fresh clone with nothing to move.
$MigrateScript = Join-Path $ProjectRoot "Backend\scripts\migrate_storage.py"
$PythonBin = $null
foreach ($candidate in @("python", "python3", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $PythonBin = $candidate
        break
    }
}

if ($PythonBin) {
    & $PythonBin $MigrateScript --auto
    if ($LASTEXITCODE -ne 0) {
        Stop-Launch "Storage migration failed. Your data has not been lost - see the error above.`nA rollback script was written to Backend\storage\_migrated-legacy\."
    }
}
else {
    Write-Warn "Python not found; skipping the storage migration check."
    Write-Warn "If this is an upgrade from an older checkout, run Backend\scripts\migrate_storage.py --execute manually."
}

#  === 6. Launch =================================

Write-Warn "Starting backend Docker containers..."
Write-Warn "Using the images already on this machine - nothing will be pulled or updated."
Write-Warn "Only containers whose config changed (storage mounts, storage env vars)"
Write-Warn "are recreated; everything else is left running as-is."
Write-Host ""

# --pull never      : never contact the registry, use the local image only.
# (no --force-recreate) : compose recreates a container only when its spec
#                     changed, so the storage volume/env edits land while
#                     unrelated services keep their current containers.
docker compose `
    --env-file $BackendEnv `
    -f $BackendCompose `
    up `
    --pull never

if ($LASTEXITCODE -ne 0) {
    Stop-Launch "docker compose exited with code $LASTEXITCODE.`nIf it complains that an image is missing, that image was never pulled on this machine - run scripts\launch.ps1 (or 'docker compose --env-file `"$BackendEnv`" -f `"$BackendCompose`" pull') once to fetch it."
}

#  === 7. Final Message =================================
Write-Host ""
Write-OK "Agents API:  http://localhost:8150"
Write-OK "Control API: http://localhost:8001"
Write-OK "MONAI Label: http://localhost:8000"
Write-OK "Orthanc:     http://localhost:8043"
Write-OK "Postgres:    localhost:5433"

Read-Host "Press Enter to close"
