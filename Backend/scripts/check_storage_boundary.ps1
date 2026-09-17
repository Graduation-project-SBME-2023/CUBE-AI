[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$BackendRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StorageRoot = [IO.Path]::GetFullPath((Join-Path $BackendRoot "storage"))
$violations = New-Object System.Collections.Generic.List[string]

function Is-Under([string]$Path, [string]$Root) {
    $full = [IO.Path]::GetFullPath($Path)
    $base = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    return $full.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)
}

function Is-AllowedSourceArtifact([string]$Path) {
    $allowedRoots = @(
        (Join-Path $BackendRoot "mcp\test_data"),
        (Join-Path $BackendRoot "agent_system\tests"),
        (Join-Path $BackendRoot "apps\radiology\tests"),
        (Join-Path $BackendRoot "apps\radiology\lib\configs")
    )
    foreach ($root in $allowedRoots) {
        if ((Test-Path -LiteralPath $root) -and (Is-Under $Path $root)) { return $true }
    }
    return $false
}

$forbiddenDirectories = @(
    (Join-Path $BackendRoot "agents_workspace"),
    (Join-Path $BackendRoot "agent_system\workspace"),
    (Join-Path $BackendRoot "agent_system\logs"),
    (Join-Path $BackendRoot "apps\radiology\General_Data"),
    (Join-Path $BackendRoot "apps\radiology\Labels"),
    (Join-Path $BackendRoot "apps\radiology\Data\output"),
    (Join-Path $BackendRoot "apps\radiology\NN_Unet"),
    (Join-Path $BackendRoot "apps\radiology\inference_jobs"),
    (Join-Path $BackendRoot "apps\radiology\logs"),
    (Join-Path $BackendRoot "agent_system\agents\literature_review_agent\.knowledge_cache"),
    (Join-Path $BackendRoot "agent_system\agents\literature_review_agent\literature_review_output")
)
foreach ($directory in $forbiddenDirectories) {
    if ((Test-Path -LiteralPath $directory) -and (Get-ChildItem -LiteralPath $directory -Force -Recurse | Select-Object -First 1)) {
        $violations.Add($directory)
    }
}

$modelsRoot = Join-Path $BackendRoot "models"
if (Test-Path -LiteralPath $modelsRoot) {
    Get-ChildItem -LiteralPath $modelsRoot -Force -Recurse -File | Where-Object {
        -not (Is-Under $_.FullName (Join-Path $modelsRoot "configs")) -and $_.Name -ne ".keep"
    } | ForEach-Object { $violations.Add($_.FullName) }
}

# Step K — retired workspace sub-paths (reports/ and outputs/literature/).
# The pattern matches files under workspace_<id>/reports/ and workspace_<id>/outputs/literature/.
$workspacesRoot = Join-Path $StorageRoot "hypotheses"
if (Test-Path -LiteralPath $workspacesRoot) {
    Get-ChildItem -LiteralPath $workspacesRoot -Directory -Force | Where-Object { $_.Name -like "workspace_*" } | ForEach-Object {
        $ws = $_.FullName
        # reports/*.md, reports/*.tex, reports/*.pdf — retired to reports.source_text /
        foreach ($reportPattern in @("*.md", "*.tex", "*.pdf")) {
            $reportsGlob = Join-Path $ws (Join-Path "reports" $reportPattern)
            Get-ChildItem -Path $reportsGlob -Force -ErrorAction SilentlyContinue | ForEach-Object {
                $violations.Add($_.FullName + " [retired to reports.source_text by K]")
            }
        }
        # outputs/literature/* — retired to literature_reviews + literature_papers
        $litDir = Join-Path $ws "outputs\literature"
        if (Test-Path -LiteralPath $litDir) {
            Get-ChildItem -LiteralPath $litDir -Force -Recurse -File | ForEach-Object {
                $violations.Add($_.FullName + " [retired to literature_reviews/papers by K]")
            }
        }
        # legacy literature_review_output/ inside a workspace
        $legacyLitDir = Join-Path $ws "literature_review_output"
        if (Test-Path -LiteralPath $legacyLitDir) {
            Get-ChildItem -LiteralPath $legacyLitDir -Force -Recurse -File | ForEach-Object {
                $violations.Add($_.FullName + " [retired to literature_reviews/papers by K]")
            }
        }
        # workspace cache/ is retired; literature cache is shared at storage/cache/literature
        $workspaceCacheDir = Join-Path $ws "cache"
        if (Test-Path -LiteralPath $workspaceCacheDir) {
            Get-ChildItem -LiteralPath $workspaceCacheDir -Force -Recurse | ForEach-Object {
                $violations.Add($_.FullName + " [retired to shared storage/cache/literature]")
            }
        }
    }
}

$runtimeDirectoryNames = @(".knowledge_cache", "literature_review_output", "jpeg_slice_files", "results")
Get-ChildItem -LiteralPath $BackendRoot -Directory -Recurse -Force | Where-Object {
    -not (Is-Under $_.FullName $StorageRoot) -and
    -not (Is-AllowedSourceArtifact $_.FullName) -and
    ($_.Name -in $runtimeDirectoryNames -or $_.Name -like "workspace_*" -or $_.Name -like "tmp????????")
} | ForEach-Object { $violations.Add($_.FullName) }

Get-ChildItem -LiteralPath $BackendRoot -File -Recurse -Force | Where-Object {
    if (Is-Under $_.FullName $StorageRoot) { return $false }
    if (Is-AllowedSourceArtifact $_.FullName) { return $false }
    if ($_.FullName -match '[\\\/](venv|node_modules|__pycache__|\.pytest_cache)[\\\/]') { return $false }
    return (
        $_.Name -like "*.nii.gz" -or $_.Extension -in @(".gif", ".pt", ".pth") -or
        $_.Name -like "execution_*.log"
    )
} | ForEach-Object { $violations.Add($_.FullName) }

$unique = @($violations | Sort-Object -Unique)
if ($unique.Count) {
    Write-Error "Runtime artifacts exist outside Backend/storage:`n$($unique -join "`n")"
    exit 1
}

Write-Host "Backend storage boundary is clean."
exit 0
