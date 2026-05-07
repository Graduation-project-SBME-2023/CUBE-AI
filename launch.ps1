# ─────────────────────────────────────────────
#  OHIF-Standalone launcher  ·  Production / GPU
#  Double-click this file to launch
# ─────────────────────────────────────────────

# Keep the window open if something goes wrong
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-OK    { param($m) Write-Host "[✔] $m" -ForegroundColor Green  }
function Write-Warn  { param($m) Write-Host "[!] $m" -ForegroundColor Yellow }
function Stop-Launch { param($m) Write-Host "[✘] $m" -ForegroundColor Red; Write-Host; Read-Host "Press Enter to close"; exit 1 }

Write-Host ""
Write-Host "  OHIF-Standalone  ·  Production launcher"
Write-Host "  -----------------------------------------"
Write-Host ""

# ── 1. Docker ────────────────────────────────
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-Launch "Docker is not installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ then re-run this script."
}

try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -ne 0) { throw }
} catch {
    Stop-Launch "Docker Desktop is not running.`nPlease open Docker Desktop, wait until it shows 'Docker is running', then double-click this script again."
}
Write-OK "Docker is running."

# ── 2. Ollama ────────────────────────────────
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Stop-Launch "Ollama is not installed. Download it from https://ollama.com/download then re-run this script."
}

$ollamaOk = $false
try {
    $resp = Invoke-WebRequest -Uri "http://localhost:11434" -UseBasicParsing -TimeoutSec 3 -ErrorAction SilentlyContinue
    if ($resp.StatusCode -lt 500) { $ollamaOk = $true }
} catch {}

if (-not $ollamaOk) {
    try {
        $null = ollama list 2>&1
        if ($LASTEXITCODE -eq 0) { $ollamaOk = $true }
    } catch {}
}

if (-not $ollamaOk) {
    Stop-Launch "Ollama does not appear to be running.`nOpen the Ollama app from your Start menu, wait a moment, then double-click this script again."
}
Write-OK "Ollama is running."

# ── 3. Environment variable ───────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$env:COMPOSE_PROJECT_DIR = $ScriptDir
Write-OK "COMPOSE_PROJECT_DIR set to: $ScriptDir"

# ── 4. Launch ─────────────────────────────────
Write-Host ""
Write-Warn "Starting containers (GPU · production mode)..."
Write-Warn "First run will download images — this may take several minutes."
Write-Host ""

docker compose `
    -f "$ScriptDir\Frontend\OHIF-Viewer\docker-compose.frontend.prod.yml" `
    -f "$ScriptDir\Backend\docker-compose.backend.gpu.yml" `
    up

if ($LASTEXITCODE -ne 0) {
    Stop-Launch "docker compose exited with code $LASTEXITCODE. Check the output above for errors."
}

Write-Host ""
Write-OK "Application is available at http://localhost"
Read-Host "Press Enter to close"