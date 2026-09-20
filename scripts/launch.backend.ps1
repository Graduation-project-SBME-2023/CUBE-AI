#  =========================================
#  OHIF-Standalone Backend-only launcher
#  Double-click this file to launch
#
#  - No frontend: use launch.ps1 for both.
#  - No image updates: reuses whatever images are already on this
#    machine (--pull missing) and only pulls one that's missing entirely.
#    Use launch.ps1 when you actually want to fetch newer images.
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

# Runs an external command and bounds it with a hard timeout, killing it if it
# doesn't finish - 'docker info' and especially 'ollama list' can hang
# indefinitely while their backend is still starting up, which would otherwise
# freeze this whole launcher with no error and no output.
function Test-ProcessSucceeds {
    param(
        [Parameter(Mandatory)] [string] $FileName,
        [string] $Arguments = "",
        [int] $TimeoutMs = 5000
    )
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $FileName
        $psi.Arguments = $Arguments
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $proc = [System.Diagnostics.Process]::Start($psi)
        if ($proc.WaitForExit($TimeoutMs)) {
            return ($proc.ExitCode -eq 0)
        }
        try { $proc.Kill() } catch {}
        return $false
    }
    catch { return $false }
}

Write-Host ""
Write-Host "  ========================================="
Write-Host "  OHIF-Standalone Backend-only launcher"
Write-Host "  ========================================="
Write-Host ""

# Check every required application before trying to start either one so a new
# user gets the complete installation list in a single run.
$missingPrerequisites = @()
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $missingPrerequisites += "Docker Desktop: https://www.docker.com/products/docker-desktop/"
}
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    $missingPrerequisites += "Ollama: https://ollama.com/download"
}

if ($missingPrerequisites.Count -gt 0) {
    Write-Warn "The following required software is not installed or is not available in PATH:"
    foreach ($prerequisite in $missingPrerequisites) {
        Write-Host "  - $prerequisite" -ForegroundColor Yellow
    }
    Stop-Launch "Install the missing software, open it once to finish setup, then re-run this script. If it is already installed, restart this terminal so PATH is refreshed."
}

Write-OK "Docker is installed."
Write-OK "Ollama is installed."

# -- 1. Docker --------------------------------
# Don't make the user open Docker Desktop by hand - start it here and wait.
function Test-DockerReady {
    return Test-ProcessSucceeds -FileName "docker" -Arguments "info" -TimeoutMs 10000
}

# Launch the Docker Desktop app whenever its process isn't already running -
# even if the engine already answers (e.g. a lingering WSL daemon) - so the
# app actually opens instead of silently relying on a background engine.
$dockerAppRunning = $null -ne (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)
if (-not $dockerAppRunning) {
    $dockerExe = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Docker\Docker\Docker Desktop.exe"),
        (Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1

    if ($dockerExe) {
        Write-Warn "Docker Desktop is not open - launching it..."
        Start-Process -FilePath $dockerExe | Out-Null
    }
    elseif (-not (Test-DockerReady)) {
        # No Docker Desktop install found and the engine isn't reachable either;
        # try the CLI-managed engine (Docker CE / WSL) as a last resort.
        Start-Process -FilePath "com.docker.cli" -ArgumentList "engine start" -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
    }
}

if (-not (Test-DockerReady)) {
    Write-Warn "Waiting for the Docker engine to come up (up to 180s)..."
    $startTime = Get-Date
    $deadline = $startTime.AddSeconds(180)
    $nextHeartbeat = $startTime.AddSeconds(15)
    while (-not (Test-DockerReady)) {
        $now = Get-Date
        if ($now -gt $deadline) {
            Stop-Launch "Docker did not become ready within 180 seconds.`nOpen Docker Desktop manually, wait until it shows 'Docker is running', then re-run this script."
        }
        if ($now -gt $nextHeartbeat) {
            Write-Warn "  ...still waiting on Docker ($([int]($now - $startTime).TotalSeconds)s elapsed)"
            $nextHeartbeat = $now.AddSeconds(15)
        }
        Start-Sleep -Seconds 3
    }
}

Write-OK "Docker is running."

#  = 2. Ollama ==================================
# Same idea: if Ollama isn't answering, start it ourselves. Prefer the desktop
# app when it's installed, otherwise run a headless 'ollama serve'.
function Test-OllamaReady {
    # The HTTP port is the only signal that actually matters: it's exactly
    # what the agents container talks to (OLLAMA_BASE_URL=http://host.docker.internal:11434).
    # 'ollama list' is NOT used here - on some installs it silently launches
    # the app/server as a side effect just by being invoked, and on others it
    # can exit 0 even when nothing is actually listening, so its exit code is
    # not a trustworthy readiness signal either way.
    try {
        $resp = Invoke-WebRequest `
            -Uri "http://localhost:11434" `
            -UseBasicParsing `
            -TimeoutSec 3 `
            -ErrorAction SilentlyContinue

        if ($resp.StatusCode -lt 500) { return $true }
    }
    catch {}

    return $false
}

if (-not (Test-OllamaReady)) {
    Write-Warn "Ollama is not responding - starting it now..."

    $ollamaApp = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
    if (Test-Path -LiteralPath $ollamaApp) {
        Start-Process -FilePath $ollamaApp | Out-Null
    }
    else {
        Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden | Out-Null
    }

    Write-Warn "Waiting for Ollama to accept connections (up to 60s)..."
    $startTime = Get-Date
    $deadline = $startTime.AddSeconds(60)
    $nextHeartbeat = $startTime.AddSeconds(10)
    while (-not (Test-OllamaReady)) {
        $now = Get-Date
        if ($now -gt $deadline) {
            Stop-Launch "Ollama did not become ready within 60 seconds.`nStart it manually (run 'ollama serve' or open the Ollama app), then re-run this script."
        }
        if ($now -gt $nextHeartbeat) {
            Write-Warn "  ...still waiting on Ollama ($([int]($now - $startTime).TotalSeconds)s elapsed)"
            $nextHeartbeat = $now.AddSeconds(10)
        }
        Start-Sleep -Seconds 3
    }
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

#  === 4. Backend compose (GPU only) =======================
# The CPU backend variant has been removed - this stack is GPU-only.
$BackendCompose = Join-Path $ProjectRoot "deploy\docker-compose.backend.gpu.yml"
$BackendMode = "GPU"

Write-Host ""
Write-OK "Backend mode: $BackendMode"
Write-Warn "Frontend will NOT be started. Run scripts\launch.ps1 if you need the viewer."
Write-Host ""

#  === 5. Storage migration =================================
# Relocates pre-restructure runtime data into Backend/storage. Silent no-op
# once migrated, and on a fresh clone with nothing to move.
$MigrateScript = Join-Path $ProjectRoot "Backend\scripts\migrate_storage.py"
$PythonBin = $null
foreach ($candidate in @("python", "python3", "py")) {
    # Get-Command on its own is not a usable test here. Windows ships zero-byte
    # "app execution alias" stubs for python.exe/python3.exe under
    # %LOCALAPPDATA%\Microsoft\WindowsApps, and that directory is on PATH by
    # default - so on a machine with no Python at all, Get-Command still resolves
    # one. Running it only prints "Python was not found; run without arguments to
    # install from the Microsoft Store..." and exits 9009, which the check below
    # then reported as a failed storage migration and turned into a Stop-Launch,
    # killing the launcher before a single container started. The migration is a
    # no-op on such machines anyway, so actually execute the interpreter: a real
    # one passes, a stub fails and we fall through to the skip branch below.
    if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) {
        continue
    }
    if (Test-ProcessSucceeds -FileName $candidate -Arguments "-c pass" -TimeoutMs 20000) {
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
Write-Warn "Images already on this machine are reused as-is; only ones missing entirely are pulled."
Write-Warn "Only containers whose config changed (storage mounts, storage env vars)"
Write-Warn "are recreated; everything else is left running as-is."
Write-Host ""

# --pull missing    : reuse the pre-built images (agents, monailabel, orthanc,
#                     postgres) already on this machine as-is; only pull one
#                     that isn't present at all (e.g. first run, or an image
#                     that was since removed). Never re-pulls/updates an image
#                     that's already here - `--pull never` used to be here,
#                     but that hard-fails with "No such image" the moment any
#                     required image hasn't been pulled yet.
# --build           : application-api/db-migrate/db-seed have a `build:`
#                     section (pull_policy: build) and are meant to always
#                     run the current checkout's code; without --build,
#                     `up` can reuse an already-built local image tag and
#                     silently run stale migrations/code instead of what's
#                     on disk (see database_audit.md D-33). --build only
#                     affects services that declare `build:`, so it does not
#                     change the "nothing pulled" behavior for the rest.
# (no --force-recreate) : compose recreates a container only when its spec
#                     changed, so the storage volume/env edits land while
#                     unrelated services keep their current containers.
docker compose `
    --env-file $BackendEnv `
    -f $BackendCompose `
    up `
    --pull missing `
    --build `
    --detach `
    --wait `
    --wait-timeout 3600

$ComposeExit = $LASTEXITCODE

# `docker compose up --wait` returns a non-zero code whenever a run-to-completion
# service exits - even with code 0. This stack has three such one-shots: db-migrate,
# db-seed, and startup-complete (the readiness announcer). startup-complete is wired
# to start only after every dependency reports healthy, so its exit code - not the
# aggregate --wait code - is the real readiness signal.
$StartupId = docker compose `
    --env-file $BackendEnv `
    -f $BackendCompose `
    ps -aq startup-complete | Select-Object -First 1

if ($StartupId) {
    $StartupExit = [int](docker inspect --format '{{.State.ExitCode}}' $StartupId)
    if ($StartupExit -ne 0) {
        Stop-Launch "Stack did not come up cleanly: readiness check (startup-complete) exited with code $StartupExit. Check the output above for errors."
    }
}
elseif ($ComposeExit -ne 0) {
    Stop-Launch "docker compose exited with code $ComposeExit.`nIf it complains that an image is missing, that image was never pulled on this machine - run scripts\launch.ps1 (or 'docker compose --env-file `"$BackendEnv`" -f `"$BackendCompose`" pull') once to fetch it."
}

#  === 7. Final Message =================================
Write-Host ""

# Show the end of each service's startup output before the single project-level
# readiness message. --tail 0 below prevents these lines from being repeated.
docker compose `
    --env-file $BackendEnv `
    -f $BackendCompose `
    logs `
    --tail 20

Write-Host ""
Write-OK "Agents API:  http://localhost:8150"
Write-OK "Application API: http://localhost:8001"
Write-OK "MONAI Label: http://localhost:8000"
Write-OK "Orthanc:     http://localhost:8043"
Write-OK "Postgres:    localhost:5433"
Write-Warn "Following container logs. Press Ctrl+C to stop viewing logs; containers will keep running."
Write-Host ""

docker compose `
    --env-file $BackendEnv `
    -f $BackendCompose `
    logs `
    --follow `
    --tail 0

Read-Host "Press Enter to close"
