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

#  === 3. Environment Variable ================================
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$env:COMPOSE_PROJECT_DIR = $ScriptDir

Write-OK "COMPOSE_PROJECT_DIR set to: $ScriptDir"

#  === 4. Backend Selection =================================
Write-Host ""
Write-Host "Select backend mode:"
Write-Host "  1) GPU"
Write-Host "  2) CPU"
Write-Host ""

while ($true) {
    $BackendChoice = Read-Host "Choice [1-2]"

    switch ($BackendChoice) {
        "1" {
            $BackendCompose = Join-Path $ScriptDir "Backend\docker-compose.backend.gpu.yml"
            $BackendMode = "GPU"
            break
        }

        "2" {
            $BackendCompose = Join-Path $ScriptDir "Backend\docker-compose.backend.cpu.yml"
            $BackendMode = "CPU"
            break
        }

        default {
            Write-Warn "Invalid selection."
        }
    }
}

#  === 5. Frontend Selection =================================
Write-Host ""
Write-Host "Select frontend mode:"
Write-Host "  1) Docker (production)"
Write-Host "  2) Yarn (development)"
Write-Host ""

while ($true) {
    $FrontendChoice = Read-Host "Choice [1-2]"

    switch ($FrontendChoice) {
        "1" {
            $FrontendMode = "Docker"
            $FrontendCompose = Join-Path $ScriptDir "Frontend\OHIF-Viewer\docker-compose.frontend.prod.yml"
            break
        }

        "2" {
            $FrontendMode = "Yarn"
            break
        }

        default {
            Write-Warn "Invalid selection."
        }
    }
}

Write-Host ""
Write-OK "Selected backend: $BackendMode"
Write-OK "Selected frontend: $FrontendMode"
Write-Host ""

#  === 6. Launch =================================

if ($FrontendMode -eq "Docker") {

    Write-Warn "Starting Docker containers..."
    Write-Warn "First run may take several minutes."
    Write-Host ""

    docker compose `
        -f $FrontendCompose `
        -f $BackendCompose `
        up

    if ($LASTEXITCODE -ne 0) {
        Stop-Launch "docker compose exited with code $LASTEXITCODE. Check the output above for errors."
    }
}
else {

    #  === Yarn checks =================================
    if (-not (Get-Command yarn -ErrorAction SilentlyContinue)) {
        Stop-Launch "Yarn is not installed.`nInstall it with:`nnpm install -g yarn"
    }

    $FrontendDir = Join-Path $ScriptDir "Frontend\OHIF-Viewer"

    if (-not (Test-Path $FrontendDir)) {
        Stop-Launch "Frontend directory not found:`n$FrontendDir"
    }

    Write-Warn "Starting backend containers..."

    docker compose `
        -f $BackendCompose `
        up -d

    if ($LASTEXITCODE -ne 0) {
        Stop-Launch "Failed to start backend containers."
    }

    Write-Host ""
    Write-Warn "Starting OHIF frontend with Yarn..."
    Write-Host ""

    Set-Location $FrontendDir

    yarn run dev

    if ($LASTEXITCODE -ne 0) {
        Stop-Launch "yarn run dev exited with code $LASTEXITCODE."
    }
}

#  === 7. Final Message =================================
Write-Host ""

if ($FrontendMode -eq "Docker") {
    Write-OK "Frontend: http://localhost"
}
else {
    Write-OK "Frontend: http://localhost:3000"
}

Read-Host "Press Enter to close"