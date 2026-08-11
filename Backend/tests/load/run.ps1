[CmdletBinding()]
param(
    [ValidateSet("smoke", "load", "stress")]
    [string]$Profile = "smoke",

    [ValidateSet("docker", "local")]
    [string]$Runtime = "docker",

    [string]$EnabledServices = "frontend,agents,control,monai,orthanc",
    [string]$FrontendUrl,
    [string]$AgentsUrl,
    [string]$ControlUrl,
    [string]$MonaiUrl,
    [string]$OrthancUrl,
    [string]$OrthancUser = $env:ORTHANC_USER,
    [string]$OrthancPassword = $env:ORTHANC_PASSWORD,
    [switch]$AllowRemoteTargets,
    [string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $PSScriptRoot "results"
}

$profiles = @{
    smoke = @{ Vus = 1;  Warmup = "2s";  Duration = "10s"; Cooldown = "2s" }
    load  = @{ Vus = 10; Warmup = "15s"; Duration = "2m";  Cooldown = "15s" }
    stress = @{ Vus = 50; Warmup = "30s"; Duration = "5m";  Cooldown = "30s" }
}

$hostName = if ($Runtime -eq "docker") { "host.docker.internal" } else { "localhost" }
if (-not $FrontendUrl) { $FrontendUrl = "http://$hostName" }
if (-not $AgentsUrl) { $AgentsUrl = "http://${hostName}:8150" }
if (-not $ControlUrl) { $ControlUrl = "http://${hostName}:8001" }
if (-not $MonaiUrl) { $MonaiUrl = "http://${hostName}:8000" }
if (-not $OrthancUrl) { $OrthancUrl = "http://${hostName}:8043" }

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null

$settings = $profiles[$Profile]
$k6Environment = @(
    "ENABLED_SERVICES=$EnabledServices",
    "FRONTEND_URL=$FrontendUrl",
    "AGENTS_URL=$AgentsUrl",
    "CONTROL_URL=$ControlUrl",
    "MONAI_URL=$MonaiUrl",
    "ORTHANC_URL=$OrthancUrl",
    "VUS=$($settings.Vus)",
    "WARMUP=$($settings.Warmup)",
    "DURATION=$($settings.Duration)",
    "COOLDOWN=$($settings.Cooldown)",
    "ALLOW_REMOTE_TARGETS=$($AllowRemoteTargets.IsPresent.ToString().ToLowerInvariant())"
)

if ($OrthancUser) { $k6Environment += "ORTHANC_USER=$OrthancUser" }
if ($OrthancPassword) { $k6Environment += "ORTHANC_PASSWORD=$OrthancPassword" }

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$summaryName = "system-load-$Profile-$timestamp.json"
$scriptPath = Join-Path $PSScriptRoot "system-load-test.js"

Write-Host "Running the '$Profile' profile against:"
Write-Host "  Frontend: $FrontendUrl"
Write-Host "  Agents:   $AgentsUrl"
Write-Host "  Control:  $ControlUrl"
Write-Host "  MONAI:    $MonaiUrl"
Write-Host "  Orthanc:  $OrthancUrl"
Write-Host "  Services: $EnabledServices"

if ($Runtime -eq "docker") {
    $dockerArgs = @("run", "--rm")
    foreach ($entry in $k6Environment) {
        $dockerArgs += @("-e", $entry)
    }
    $dockerArgs += @(
        "-v", "${PSScriptRoot}:/scripts:ro",
        "-v", "${resolvedOutput}:/results",
        "grafana/k6:0.55.0",
        "run",
        "--summary-export", "/results/$summaryName",
        "/scripts/system-load-test.js"
    )
    & docker @dockerArgs
} else {
    if (-not (Get-Command k6 -ErrorAction SilentlyContinue)) {
        throw "k6 was not found. Install k6 or run with -Runtime docker."
    }

    $k6Args = @("run")
    foreach ($entry in $k6Environment) {
        $separator = $entry.IndexOf("=")
        $name = $entry.Substring(0, $separator)
        $value = $entry.Substring($separator + 1)
        $k6Args += @("-e", "$name=$value")
    }
    $k6Args += @("--summary-export", (Join-Path $resolvedOutput $summaryName), $scriptPath)
    & k6 @k6Args
}

if ($LASTEXITCODE -ne 0) {
    throw "Load test failed with exit code $LASTEXITCODE. Check the preflight and threshold output above."
}

Write-Host "Load test passed. Summary: $(Join-Path $resolvedOutput $summaryName)"
