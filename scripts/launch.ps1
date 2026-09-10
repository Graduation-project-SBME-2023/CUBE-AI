#  ====================================
#  OHIF-Standalone Interactive launcher
#  Double-click this file to launch
#  ====================================

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
Write-Host "  ===================================="
Write-Host "  OHIF-Standalone Interactive launcher"
Write-Host "  ===================================="
Write-Host ""

# ── 1. Docker ────────────────────────────────
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
$BackendEnv = Join-Path $ProjectRoot "deploy\.env"
$env:COMPOSE_PROJECT_DIR = $ProjectRoot

if (-not (Test-Path -LiteralPath $BackendEnv -PathType Leaf)) {
    Stop-Launch "Backend environment file not found: $BackendEnv`nCopy deploy\.env.example to deploy\.env and try again."
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

$FrontendCompose = Join-Path $ProjectRoot "deploy\docker-compose.frontend.prod.yml"

Write-Host ""
Write-OK "Selected backend: $BackendMode"
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

Write-Warn "Starting Docker containers..."
Write-Warn "First run may take several minutes."
Write-Host ""

# --build: application-api/db-migrate/db-seed have a `build:` section and
# are meant to always run the current checkout's code. Without it, `up` can
# reuse an already-built local image tag and silently run stale
# migrations/code instead of what's on disk (see database_audit.md D-33).
docker compose `
    --env-file $BackendEnv `
    -f $BackendCompose `
    -f $FrontendCompose `
    up `
    --build `
    --detach `
    --wait `
    --wait-timeout 3600

$ComposeExit = $LASTEXITCODE

# `docker compose up --wait` returns a non-zero code whenever a run-to-completion
# service exits - even with code 0. This stack has three such one-shots: db-migrate,
# db-seed, and startup-complete (the readiness announcer). startup-complete is wired
# to start only after every backend and viewer dependency reports healthy, so its
# exit code - not the aggregate --wait code - is the real readiness signal.
$StartupId = docker compose `
    --env-file $BackendEnv `
    -f $BackendCompose `
    -f $FrontendCompose `
    ps -aq startup-complete | Select-Object -First 1

if ($StartupId) {
    $StartupExit = [int](docker inspect --format '{{.State.ExitCode}}' $StartupId)
    if ($StartupExit -ne 0) {
        Stop-Launch "Stack did not come up cleanly: readiness check (startup-complete) exited with code $StartupExit. Check the output above for errors."
    }
}
elseif ($ComposeExit -ne 0) {
    Stop-Launch "docker compose exited with code $ComposeExit. Check the output above for errors."
}

#  === 7. Final Message =================================
Write-Host ""

# Show the end of each service's startup output before the single project-level
# readiness message. --tail 0 below prevents these lines from being repeated.
docker compose `
    --env-file $BackendEnv `
    -f $BackendCompose `
    -f $FrontendCompose `
    logs `
    --tail 20

Write-Host ""
Write-OK "Frontend: http://localhost"
Write-Warn "Following container logs. Press Ctrl+C to stop viewing logs; containers will keep running."
Write-Host ""

docker compose `
    --env-file $BackendEnv `
    -f $BackendCompose `
    -f $FrontendCompose `
    logs `
    --follow `
    --tail 0

Read-Host "Press Enter to close"
