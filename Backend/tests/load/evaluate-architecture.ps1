[CmdletBinding()]
param(
    [ValidateSet("quick", "standard")]
    [string]$Profile = "quick",
    [string]$MonolithUrl = "http://host.docker.internal:8000",
    [string]$ControlUrl = "http://host.docker.internal:8001",
    [string]$InferenceUrl = "http://host.docker.internal:8000",
    [string]$InferenceModel = "segmentation_spleen",
    [switch]$AllowRemoteTargets,
    [switch]$SkipInference,
    [ValidateRange(0, 60)]
    [int]$CooldownSeconds = 10,
    [string]$ReuseTimestamp,
    [string]$OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $PSScriptRoot "results"
}
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null

$profiles = @{
    quick = @{ Duration = "15s"; Rate = 1; InferenceVus = 1 }
    standard = @{ Duration = "60s"; Rate = 3; InferenceVus = 2 }
}
$settings = $profiles[$Profile]
$timestamp = if ($ReuseTimestamp) { $ReuseTimestamp } else { Get-Date -Format "yyyyMMdd-HHmmss" }
$scriptPath = "${PSScriptRoot}:/scripts:ro"

function Invoke-EvaluationPhase {
    param(
        [string]$Name,
        [string]$DataUrl,
        [bool]$WithInference
    )

    $summaryName = "architecture-$timestamp-$Name.json"
    $environment = @(
        "DATA_URL=$DataUrl",
        "INFERENCE_URL=$InferenceUrl",
        "INFERENCE_MODEL=$InferenceModel",
        "ENABLE_INFERENCE=$($WithInference.ToString().ToLowerInvariant())",
        "DATA_DURATION=$($settings.Duration)",
        "DATA_RATE=$($settings.Rate)",
        "INFERENCE_VUS=$($settings.InferenceVus)",
        "ALLOW_REMOTE_TARGETS=$($AllowRemoteTargets.IsPresent.ToString().ToLowerInvariant())"
    )

    Write-Host "`n=== $Name ==="
    Write-Host "Data API: $DataUrl; inference: $WithInference"
    $arguments = @("run", "--rm")
    foreach ($entry in $environment) {
        $arguments += @("-e", $entry)
    }
    $arguments += @(
        "-v", $scriptPath,
        "-v", "${resolvedOutput}:/results",
        "grafana/k6:0.55.0",
        "run",
        "--summary-export", "/results/$summaryName",
        "/scripts/architecture-evaluation.js"
    )

    & docker @arguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Architecture phase '$Name' failed with exit code $LASTEXITCODE."
    }
    return Join-Path $resolvedOutput $summaryName
}

function Get-MetricValue {
    param($Summary, [string]$Metric, [string]$Property, [double]$Default = 0)
    $metricEntry = $Summary.metrics.PSObject.Properties[$Metric]
    if ($null -eq $metricEntry) { return $Default }
    $propertyEntry = $metricEntry.Value.PSObject.Properties[$Property]
    if ($null -eq $propertyEntry) { return $Default }
    return [double]$propertyEntry.Value
}

function Get-PhaseResult {
    param([string]$Name, [string]$Path)
    $summary = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    return [pscustomobject]@{
        Name = $Name
        DataP95 = Get-MetricValue $summary "data_latency" "p(95)"
        DataAverage = Get-MetricValue $summary "data_latency" "avg"
        DataFailureRate = Get-MetricValue $summary "data_failures" "value"
        DataRequests = Get-MetricValue $summary "data_requests" "count"
        DroppedIterations = Get-MetricValue $summary "dropped_iterations" "count"
        InferenceP95 = Get-MetricValue $summary "inference_latency" "p(95)"
        InferenceFailureRate = Get-MetricValue $summary "inference_failures" "value"
        InferenceRequests = Get-MetricValue $summary "inference_requests" "count"
        Path = $Path
    }
}

function Get-Ratio {
    param([double]$Numerator, [double]$Denominator)
    if ($Denominator -le 0) { return 0 }
    return $Numerator / $Denominator
}

$paths = @{}
if ($ReuseTimestamp) {
    foreach ($name in @("monolith-idle", "control-idle")) {
        $paths[$name] = Join-Path $resolvedOutput "architecture-$timestamp-$name.json"
    }
    if (-not $SkipInference) {
        foreach ($name in @("monolith-contention", "control-contention")) {
            $paths[$name] = Join-Path $resolvedOutput "architecture-$timestamp-$name.json"
        }
    }
    foreach ($path in $paths.Values) {
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Cannot reuse missing summary: $path"
        }
    }
} else {
    $paths["monolith-idle"] = Invoke-EvaluationPhase "monolith-idle" $MonolithUrl $false
    if ($CooldownSeconds -gt 0) { Start-Sleep -Seconds $CooldownSeconds }
    $paths["control-idle"] = Invoke-EvaluationPhase "control-idle" $ControlUrl $false

    if (-not $SkipInference) {
        if ($CooldownSeconds -gt 0) { Start-Sleep -Seconds $CooldownSeconds }
        $paths["monolith-contention"] = Invoke-EvaluationPhase "monolith-contention" $MonolithUrl $true
        if ($CooldownSeconds -gt 0) { Start-Sleep -Seconds $CooldownSeconds }
        $paths["control-contention"] = Invoke-EvaluationPhase "control-contention" $ControlUrl $true
    }
}

$results = @()
foreach ($entry in $paths.GetEnumerator() | Sort-Object Name) {
    $results += Get-PhaseResult $entry.Name $entry.Value
}

$monolithIdle = $results | Where-Object Name -eq "monolith-idle"
$controlIdle = $results | Where-Object Name -eq "control-idle"
$monolithContention = $results | Where-Object Name -eq "monolith-contention"
$controlContention = $results | Where-Object Name -eq "control-contention"

$verdict = "INCONCLUSIVE"
$reason = "Inference contention was not tested."
$monolithInterference = 0
$controlInterference = 0
$isolationBenefit = 0
$monolithFailureIncrease = 0
$controlFailureIncrease = 0
$isolationFailureBenefit = 0

if (-not $SkipInference) {
    $monolithInterference = Get-Ratio $monolithContention.DataP95 $monolithIdle.DataP95
    $controlInterference = Get-Ratio $controlContention.DataP95 $controlIdle.DataP95
    $isolationBenefit = Get-Ratio $monolithContention.DataP95 $controlContention.DataP95
    $monolithFailureIncrease = $monolithContention.DataFailureRate - $monolithIdle.DataFailureRate
    $controlFailureIncrease = $controlContention.DataFailureRate - $controlIdle.DataFailureRate
    $isolationFailureBenefit = $monolithContention.DataFailureRate - $controlContention.DataFailureRate
    $inferenceFailed =
        $monolithContention.InferenceRequests -lt 1 -or
        $controlContention.InferenceRequests -lt 1 -or
        $monolithContention.InferenceFailureRate -gt 0 -or
        $controlContention.InferenceFailureRate -gt 0

    if ($inferenceFailed) {
        $reason = "Synthetic inference did not complete successfully in both contention phases, so isolation cannot be inferred."
    } elseif (
        $controlContention.DataFailureRate -lt 0.01 -and
        (($monolithInterference -ge 1.5 -and $controlInterference -lt $monolithInterference * 0.75) -or
        ($isolationBenefit -ge 1.5 -and $monolithContention.DataP95 - $controlContention.DataP95 -ge 100) -or
        ($monolithContention.DataFailureRate -ge 0.01))
    ) {
        $verdict = "SERVICE ISOLATION JUSTIFIED"
        $reason = "Inference materially harms the monolith data path, while the separate control process reduces that interference."
    } elseif (
        $monolithContention.DataFailureRate - $controlContention.DataFailureRate -ge 0.20 -or
        ($isolationBenefit -ge 1.5 -and $controlContention.DataFailureRate -ge 0.01)
    ) {
        $verdict = "SERVICE ISOLATION HELPS BUT IS INSUFFICIENT"
        $reason = "The separate process reduces inference interference, but its data path still violates the latency/error objectives."
    } elseif (
        $monolithInterference -lt 1.25 -and
        $controlInterference -lt 1.25 -and
        $monolithContention.DataFailureRate -lt 0.01 -and
        $monolithContention.DataP95 -lt 500
    ) {
        $verdict = "NO PERFORMANCE CASE FOR A SPLIT YET"
        $reason = "The monolith remained within the initial latency/error objectives under the tested inference load."
    } else {
        $reason = "The result is mixed or close to the decision thresholds; run the standard profile and collect container/GPU utilization."
    }
}

$reportPath = Join-Path $resolvedOutput "architecture-evaluation-$timestamp.md"
$lines = @(
    "# Architecture evaluation",
    "",
    "Generated: $(Get-Date -Format o)",
    "",
    "Profile: $Profile; data arrival rate: $($settings.Rate) iterations/s (4 requests per iteration).",
    "Synthetic inference model: $InferenceModel.",
    "",
    "| Phase | Data requests | Data p95 (ms) | Data avg (ms) | Data errors | Dropped iterations | Inference requests | Inference p95 (ms) | Inference errors |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|"
)
foreach ($result in $results) {
    $lines += "| $($result.Name) | $([int]$result.DataRequests) | $([math]::Round($result.DataP95, 1)) | $([math]::Round($result.DataAverage, 1)) | $([math]::Round($result.DataFailureRate * 100, 2))% | $([int]$result.DroppedIterations) | $([int]$result.InferenceRequests) | $([math]::Round($result.InferenceP95, 1)) | $([math]::Round($result.InferenceFailureRate * 100, 2))% |"
}
$lines += @(
    "",
    "## Decision indicators",
    "",
    "- Monolith interference ratio: $([math]::Round($monolithInterference, 2))x (contention p95 / idle p95)",
    "- Control-service interference ratio: $([math]::Round($controlInterference, 2))x",
    "- Isolation benefit: $([math]::Round($isolationBenefit, 2))x (monolith contention p95 / control contention p95)",
    "- Monolith failure-rate change under inference: $([math]::Round($monolithFailureIncrease * 100, 2)) percentage points",
    "- Control failure-rate change under inference: $([math]::Round($controlFailureIncrease * 100, 2)) percentage points",
    "- Isolation failure-rate benefit during contention: $([math]::Round($isolationFailureBenefit * 100, 2)) percentage points",
    "",
    "## Verdict: $verdict",
    "",
    $reason,
    "",
    "This result can justify process/service isolation. It does **not** by itself justify MongoDB, an API gateway, or a full rewrite. Those need a candidate implementation and separate consistency, operability, and persistence benchmarks.",
    "",
    "Raw k6 summaries:"
)
foreach ($result in $results) {
    $lines += "- $($result.Name): $($result.Path)"
}
Set-Content -Path $reportPath -Value $lines -Encoding UTF8

Write-Host "`nVerdict: $verdict"
Write-Host $reason
Write-Host "Report: $reportPath"
