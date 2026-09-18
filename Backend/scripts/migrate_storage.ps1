[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Execute,
    [switch]$Verify,
    [string]$ManifestPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$selectedModeCount = @(@($DryRun, $Execute, $Verify) | Where-Object { $_ }).Count
if ($selectedModeCount -gt 1) {
    throw "Choose only one mode: -DryRun, -Execute, or -Verify."
}
if (-not $DryRun -and -not $Execute -and -not $Verify) {
    $DryRun = $true
}

$BackendRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StorageRoot = Join-Path $BackendRoot "storage"
$LegacyRoot = Join-Path $StorageRoot "_migrated-legacy"
$HashLimitBytes = 8MB
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

function Get-RelativePath([string]$BasePath, [string]$TargetPath) {
    $base = [IO.Path]::GetFullPath($BasePath).TrimEnd('\') + '\'
    $target = [IO.Path]::GetFullPath($TargetPath)
    $baseUri = [Uri]$base
    $targetUri = [Uri]$target
    return [Uri]::UnescapeDataString($baseUri.MakeRelativeUri($targetUri).ToString()).Replace('/', '\')
}

function Get-SmallFileHash([string]$Path) {
    $item = Get-Item -LiteralPath $Path
    if ($item.Length -ge $HashLimitBytes) { return $null }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

$Operations = New-Object System.Collections.Generic.List[object]
$ReservedDestinations = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
$WorkspaceDestinations = New-Object System.Collections.Generic.List[string]

function Get-ConflictDestination([string]$Origin, [string]$RelativePath) {
    $candidate = Join-Path (Join-Path $LegacyRoot $Origin) $RelativePath
    $index = 1
    while ((Test-Path -LiteralPath $candidate) -or $ReservedDestinations.Contains($candidate)) {
        $directory = Split-Path $candidate -Parent
        $leaf = Split-Path $candidate -Leaf
        $candidate = Join-Path $directory ("{0}.conflict-{1}" -f $leaf, $index)
        $index++
    }
    return $candidate
}

function Add-FileOperation(
    [string]$Source,
    [string]$Destination,
    [string]$Origin,
    [ValidateSet("Move", "Copy")][string]$Action = "Move"
) {
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) { return }
    $sourceItem = Get-Item -LiteralPath $Source
    $relative = Get-RelativePath $BackendRoot $Source
    $finalDestination = [IO.Path]::GetFullPath($Destination)
    $conflict = $false
    if ((Test-Path -LiteralPath $finalDestination) -or -not $ReservedDestinations.Add($finalDestination)) {
        $finalDestination = Get-ConflictDestination $Origin $relative
        [void]$ReservedDestinations.Add($finalDestination)
        $conflict = $true
    }
    $Operations.Add([pscustomobject]@{
        action = $Action
        source = $sourceItem.FullName
        destination = $finalDestination
        origin = $Origin
        relative_path = $relative
        size = [int64]$sourceItem.Length
        mtime_utc = $sourceItem.LastWriteTimeUtc.ToString("o")
        sha256 = Get-SmallFileHash $sourceItem.FullName
        conflict = $conflict
    })
}

function Add-TreeOperations(
    [string]$SourceRoot,
    [string]$DestinationRoot,
    [string]$Origin,
    [ValidateSet("Move", "Copy")][string]$Action = "Move"
) {
    if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) { return }
    Get-ChildItem -LiteralPath $SourceRoot -Recurse -Force -File | ForEach-Object {
        $relative = Get-RelativePath $SourceRoot $_.FullName
        Add-FileOperation $_.FullName (Join-Path $DestinationRoot $relative) $Origin $Action
    }
}

function Add-WorkspaceOperations([IO.DirectoryInfo]$WorkspaceSource) {
    $workspaceDestination = Join-Path (Join-Path $StorageRoot "hypotheses") $WorkspaceSource.Name
    $WorkspaceDestinations.Add($workspaceDestination)
    $known = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)

    $stateFiles = @(
        "state.json", "scratchpad.json", "config.json", "segmentation_manifest.json",
        "review_draft.json", "review_result.json", "literature_context.json"
    )
    foreach ($name in $stateFiles) {
        [void]$known.Add($name)
        Add-FileOperation (Join-Path $WorkspaceSource.FullName $name) (Join-Path (Join-Path $workspaceDestination "state") $name) $WorkspaceSource.Name
    }

    $directoryMap = [ordered]@{
        "populations" = "data\populations"
        "volumes" = "data\volumes"
        "masks" = "data\masks"
        "outputs" = "outputs\agents"
        "literature_review_output" = "temp\literature"
        "overlays" = "outputs\overlays"
        "features" = "outputs\features"
        "modeling" = "outputs\modeling"
        "models" = "outputs\modeling"
        "reports" = "outputs\reports"
        "temp_segmentations" = "temp\segmentations"
    }
    $workspaceCacheSource = Join-Path $WorkspaceSource.FullName ".knowledge_cache"
    if (Test-Path -LiteralPath $workspaceCacheSource) {
        Add-TreeOperations $workspaceCacheSource (Join-Path $StorageRoot "cache\literature") ($WorkspaceSource.Name + "-literature-cache")
        [void]$known.Add(".knowledge_cache")
    }

    foreach ($entry in $directoryMap.GetEnumerator()) {
        [void]$known.Add($entry.Key)
        Add-TreeOperations (Join-Path $WorkspaceSource.FullName $entry.Key) (Join-Path $workspaceDestination $entry.Value) $WorkspaceSource.Name
    }

    foreach ($clinicalName in @("clinical_data.json", "clinical_data.csv")) {
        [void]$known.Add($clinicalName)
        Add-FileOperation (Join-Path $WorkspaceSource.FullName $clinicalName) (Join-Path (Join-Path $workspaceDestination "data\populations") $clinicalName) $WorkspaceSource.Name
    }

    Get-ChildItem -LiteralPath $WorkspaceSource.FullName -Force | Where-Object {
        -not $known.Contains($_.Name)
    } | ForEach-Object {
        $legacyDestination = Join-Path (Join-Path $LegacyRoot $WorkspaceSource.Name) $_.Name
        if ($_.PSIsContainer) {
            Add-TreeOperations $_.FullName $legacyDestination $WorkspaceSource.Name
        } else {
            Add-FileOperation $_.FullName $legacyDestination $WorkspaceSource.Name
        }
    }
}

function Build-OperationPlan {
    $workspaceRoot = Join-Path $BackendRoot "agents_workspace"
    if (Test-Path -LiteralPath $workspaceRoot) {
        Get-ChildItem -LiteralPath $workspaceRoot -Directory -Filter "workspace_*" | ForEach-Object {
            Add-WorkspaceOperations $_
        }
        Get-ChildItem -LiteralPath $workspaceRoot -Force | Where-Object {
            -not $_.PSIsContainer -or -not $_.Name.StartsWith("workspace_")
        } | ForEach-Object {
            $destination = Join-Path (Join-Path $LegacyRoot "agents_workspace") $_.Name
            if ($_.PSIsContainer) {
                Add-TreeOperations $_.FullName $destination "agents_workspace"
            } else {
                Add-FileOperation $_.FullName $destination "agents_workspace"
            }
        }
    }

    $radiology = Join-Path $BackendRoot "apps\radiology"
    Add-TreeOperations (Join-Path $radiology "General_Data") (Join-Path $StorageRoot "data\medical\images") "General_Data"
    Add-TreeOperations (Join-Path $radiology "Labels") (Join-Path $StorageRoot "data\medical\labels") "Labels"
    Add-TreeOperations (Join-Path $radiology "Data\output") (Join-Path $StorageRoot "data\medical\exports") "Data-output"
    Add-TreeOperations (Join-Path $radiology "inference_jobs") (Join-Path $StorageRoot "jobs\inference") "inference_jobs"
    Add-TreeOperations (Join-Path $radiology "logs") (Join-Path $StorageRoot "logs\radiology") "radiology-logs"

    $nnunetSource = Join-Path $radiology "NN_Unet"
    foreach ($name in @("nnUNet_raw", "nnUNet_preprocessed", "nnUNet_results")) {
        Add-TreeOperations (Join-Path $nnunetSource $name) (Join-Path (Join-Path $StorageRoot "data\nnunet") $name) "NN_Unet"
    }
    if (Test-Path -LiteralPath $nnunetSource) {
        Get-ChildItem -LiteralPath $nnunetSource -Force -File | ForEach-Object {
            Add-FileOperation $_.FullName (Join-Path $StorageRoot ("data\nnunet\mappings\" + $_.Name)) "NN_Unet"
        }
    }

    $modelsSource = Join-Path $BackendRoot "models"
    Add-TreeOperations (Join-Path $modelsSource ".cache") (Join-Path $StorageRoot "cache\huggingface") "models-cache"
    Add-TreeOperations (Join-Path $modelsSource "eval") (Join-Path $StorageRoot "temp\vista\models-eval") "models-eval"
    Add-TreeOperations (Join-Path $modelsSource "configs") (Join-Path $StorageRoot "models\configs") "models-configs" "Copy"
    if (Test-Path -LiteralPath $modelsSource) {
        Get-ChildItem -LiteralPath $modelsSource -Force | Where-Object {
            $_.Name -notin @(".cache", "eval", "configs", ".keep")
        } | ForEach-Object {
            $destination = Join-Path (Join-Path $StorageRoot "models") $_.Name
            if ($_.PSIsContainer) {
                Add-TreeOperations $_.FullName $destination "models"
            } else {
                Add-FileOperation $_.FullName $destination "models"
            }
        }
    }

    Add-TreeOperations (Join-Path $BackendRoot "agent_system\logs") (Join-Path $StorageRoot "logs\agent-system") "agent-system-logs"
    Add-TreeOperations (Join-Path $BackendRoot "agent_system\agents\literature_review_agent\.knowledge_cache") (Join-Path $StorageRoot "cache\literature") "literature-cache"
    Add-TreeOperations (Join-Path $BackendRoot "agent_system\agents\literature_review_agent\literature_review_output") (Join-Path $LegacyRoot "literature_review_output") "literature-output"

    $vistaRoot = Join-Path $radiology "vista3d"
    if (Test-Path -LiteralPath $vistaRoot) {
        Get-ChildItem -LiteralPath $vistaRoot -Directory -Filter "tmp*" | ForEach-Object {
            Add-TreeOperations $_.FullName (Join-Path $StorageRoot ("temp\vista\" + $_.Name)) "vista3d"
        }
        $vistaEval = Join-Path $vistaRoot "eval"
        if (Test-Path -LiteralPath $vistaEval) {
            Get-ChildItem -LiteralPath $vistaEval -Directory -Filter "tmp*" | ForEach-Object {
                Add-TreeOperations $_.FullName (Join-Path $StorageRoot ("temp\vista\eval-" + $_.Name)) "vista3d-eval"
            }
        }
    }
}

function Get-Manifest([string]$Path) {
    if (-not $Path) {
        $latest = Get-ChildItem -LiteralPath $LegacyRoot -Filter "manifest-*.json" -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
        if (-not $latest) { throw "No migration manifest found under $LegacyRoot" }
        $Path = $latest.FullName
    }
    return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}

function Verify-Manifest([object]$Manifest) {
    $failures = New-Object System.Collections.Generic.List[string]
    $verifiedBytes = [int64]0
    foreach ($operation in $Manifest.operations) {
        if (-not (Test-Path -LiteralPath $operation.destination -PathType Leaf)) {
            $failures.Add("Missing destination: $($operation.destination)")
            continue
        }
        $item = Get-Item -LiteralPath $operation.destination
        if ([int64]$item.Length -ne [int64]$operation.size) {
            $failures.Add("Size mismatch: $($operation.destination)")
            continue
        }
        if ($operation.sha256) {
            $actualHash = (Get-FileHash -LiteralPath $operation.destination -Algorithm SHA256).Hash
            if ($actualHash -ne $operation.sha256) {
                $failures.Add("Checksum mismatch: $($operation.destination)")
                continue
            }
        }
        if ($operation.action -eq "Move" -and (Test-Path -LiteralPath $operation.source)) {
            $failures.Add("Move source still exists: $($operation.source)")
            continue
        }
        $verifiedBytes += [int64]$operation.size
    }
    if ($failures.Count) {
        $failures | ForEach-Object { Write-Error $_ }
        throw "Storage verification failed for $($failures.Count) operation(s)."
    }
    Write-Host "Verified $($Manifest.operations.Count) files ($verifiedBytes bytes) against $($Manifest.manifest_path)."
}

if ($Verify) {
    Verify-Manifest (Get-Manifest $ManifestPath)
    exit 0
}

Build-OperationPlan
$totalBytes = ($Operations | Measure-Object -Property size -Sum).Sum
$workspaceCount = $WorkspaceDestinations.Count
$conflictCount = @($Operations | Where-Object conflict).Count
Write-Host "Mode: $(if ($Execute) {'Execute'} else {'DryRun'})"
Write-Host "Workspaces: $workspaceCount"
Write-Host "Files: $($Operations.Count)"
Write-Host "Bytes: $totalBytes"
Write-Host "Conflicts routed to _migrated-legacy: $conflictCount"

if ($DryRun) {
    $Operations | Select-Object -First 30 action, source, destination, size, conflict | Format-Table -AutoSize
    if ($Operations.Count -gt 30) { Write-Host "... $($Operations.Count - 30) additional operations" }
    exit 0
}

New-Item -ItemType Directory -Path $LegacyRoot -Force | Out-Null
$manifestFile = Join-Path $LegacyRoot "manifest-$Timestamp.json"
$rollbackFile = Join-Path $LegacyRoot "rollback-$Timestamp.ps1"
$manifest = [ordered]@{
    version = 1
    created_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    backend_root = $BackendRoot
    storage_root = $StorageRoot
    manifest_path = $manifestFile
    workspace_count = $workspaceCount
    file_count = $Operations.Count
    total_bytes = [int64]$totalBytes
    hash_limit_bytes = $HashLimitBytes
    operations = $Operations
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestFile -Encoding UTF8

$rollbackLines = New-Object System.Collections.Generic.List[string]
$rollbackLines.Add('$ErrorActionPreference = "Stop"')
$rollbackLines.Add('# Generated by migrate_storage.ps1. Run only after stopping all backend writers.')
for ($index = $Operations.Count - 1; $index -ge 0; $index--) {
    $operation = $Operations[$index]
    $sourceQuoted = $operation.source.Replace("'", "''")
    $destinationQuoted = $operation.destination.Replace("'", "''")
    $rollbackLines.Add("New-Item -ItemType Directory -Path (Split-Path '$sourceQuoted' -Parent) -Force | Out-Null")
    if ($operation.action -eq "Copy") {
        $rollbackLines.Add("if (Test-Path -LiteralPath '$destinationQuoted') { Remove-Item -LiteralPath '$destinationQuoted' -Force }")
    } else {
        $rollbackLines.Add("if (Test-Path -LiteralPath '$destinationQuoted') { Move-Item -LiteralPath '$destinationQuoted' -Destination '$sourceQuoted' }")
    }
}
$rollbackLines | Set-Content -LiteralPath $rollbackFile -Encoding UTF8

foreach ($workspaceDestination in $WorkspaceDestinations) {
    foreach ($relative in @(
        "state", "data\populations", "data\volumes", "data\masks",
        "outputs\agents", "outputs\literature", "outputs\overlays",
        "outputs\features", "outputs\modeling", "outputs\reports",
        "cache\literature", "temp\segmentations"
    )) {
        New-Item -ItemType Directory -Path (Join-Path $workspaceDestination $relative) -Force | Out-Null
    }
}

foreach ($operation in $Operations) {
    New-Item -ItemType Directory -Path (Split-Path $operation.destination -Parent) -Force | Out-Null
    if ($operation.action -eq "Copy") {
        Copy-Item -LiteralPath $operation.source -Destination $operation.destination
    } else {
        Move-Item -LiteralPath $operation.source -Destination $operation.destination
    }
}

$sourceCleanupRoots = @(
    (Join-Path $BackendRoot "agents_workspace"),
    (Join-Path $BackendRoot "apps\radiology\General_Data"),
    (Join-Path $BackendRoot "apps\radiology\Labels"),
    (Join-Path $BackendRoot "apps\radiology\Data\output"),
    (Join-Path $BackendRoot "apps\radiology\NN_Unet"),
    (Join-Path $BackendRoot "apps\radiology\inference_jobs"),
    (Join-Path $BackendRoot "agent_system\logs")
)
foreach ($root in $sourceCleanupRoots) {
    if (Test-Path -LiteralPath $root) {
        Get-ChildItem -LiteralPath $root -Directory -Recurse -Force |
            Sort-Object FullName -Descending |
            Where-Object { -not (Get-ChildItem -LiteralPath $_.FullName -Force) } |
            Remove-Item -Force
    }
}

Write-Host "Migration complete. Manifest: $manifestFile"
Write-Host "Rollback script: $rollbackFile"
Write-Host "Run: & '$PSCommandPath' -Verify -ManifestPath '$manifestFile'"
