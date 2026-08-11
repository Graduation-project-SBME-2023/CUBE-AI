[CmdletBinding()]
param(
    [ValidateSet(
        "deterministic",
        "agent-regression",
        "radiology",
        "integration",
        "all",
        "list"
    )]
    [string]$Suite = "deterministic",

    [ValidateSet("docker", "local")]
    [string]$Runtime = "docker",

    [string]$Python = "python",

    [switch]$Coverage
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$AgentImage = "cubeai1/agents-app:latest"
$RadiologyImage = "cubeai1/custom-monailabel:v1.0-cpu"

$Suites = [ordered]@{
    "deterministic" = @{
        Description = "Fast unit, contract, mocked-agent, and mocked-pipeline tests."
        Image = $AgentImage
        Paths = @(
            "Backend/tests/unit",
            "Backend/tests/contracts",
            "Backend/tests/pipeline"
        )
        Marker = "unit or contract or pipeline"
        Environment = @{}
    }
    "agent-regression" = @{
        Description = "Consolidated legacy agent-system regression tests."
        Image = $AgentImage
        Paths = @("Backend/tests/regression/agent_system")
        Marker = "regression"
        Environment = @{}
    }
    "radiology" = @{
        Description = "MONAI, Torch, model-artifact, and VoxTell geometry tests."
        Image = $RadiologyImage
        Paths = @("Backend/tests/regression/radiology")
        Marker = "radiology"
        Environment = @{}
    }
    "integration" = @{
        Description = "Health checks against running backend Docker services."
        Image = $AgentImage
        Paths = @("Backend/tests/integration")
        Marker = "integration"
        Environment = @{
            RUN_BACKEND_INTEGRATION = "1"
            AGENTS_BASE_URL = "http://host.docker.internal:8150"
            APP_BASE_URL = "http://host.docker.internal:8001"
            CONTROL_BASE_URL = "http://host.docker.internal:8001"
            MONAI_BASE_URL = "http://host.docker.internal:8000"
            ORTHANC_BASE_URL = "http://host.docker.internal:8043"
        }
    }
}

function Show-Suites {
    Write-Host "Available backend test suites:"
    foreach ($entry in $Suites.GetEnumerator()) {
        Write-Host "  $($entry.Key): $($entry.Value.Description)"
        Write-Host "    $($entry.Value.Paths -join ', ')"
    }
    Write-Host "  all: deterministic + agent-regression + radiology"
}

function Get-CoverageArguments([string]$Name) {
    if (-not $Coverage -or $Name -ne "deterministic") {
        return @()
    }
    return @(
        "--cov=Backend/agent_system",
        "--cov=Backend/mcp",
        "--cov-branch",
        "--cov-report=term-missing",
        "--cov-report=xml:Backend/coverage.xml",
        "--cov-report=html:Backend/htmlcov"
    )
}

function Invoke-LocalSuite([string]$Name) {
    $definition = $Suites[$Name]
    $resultPath = "Backend/test-results/$Name.xml"
    New-Item -ItemType Directory -Force -Path (
        Join-Path $RepoRoot "Backend\test-results"
    ) | Out-Null
    $pytestArgs = @(
        "-m", "pytest",
        "-c", "Backend/pytest.ini"
    ) + $definition.Paths + @(
        "-m", $definition.Marker,
        "--junitxml=$resultPath"
    ) + (Get-CoverageArguments $Name)

    foreach ($environmentEntry in $definition.Environment.GetEnumerator()) {
        Set-Item -Path "Env:$($environmentEntry.Key)" -Value $environmentEntry.Value
    }

    Write-Host "Running '$Name' locally with $Python"
    & $Python @pytestArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Suite '$Name' failed with exit code $LASTEXITCODE."
    }
}

function Invoke-DockerSuite([string]$Name) {
    $definition = $Suites[$Name]
    $resultPath = "Backend/test-results/$Name.xml"
    $pathText = $definition.Paths -join " "
    $coverageText = (Get-CoverageArguments $Name) -join " "
    $testCommand = (
        "mkdir -p Backend/test-results " +
        "&& python -m pip install --quiet -r Backend/requirements/test.txt " +
        "&& python -m pytest -c Backend/pytest.ini $pathText " +
        "-m '$($definition.Marker)' --junitxml=$resultPath $coverageText"
    ).Trim()

    $dockerArgs = @(
        "run", "--rm",
        "-v", "${RepoRoot}:/workspace",
        "-w", "/workspace"
    )
    foreach ($environmentEntry in $definition.Environment.GetEnumerator()) {
        $dockerArgs += @(
            "-e",
            "$($environmentEntry.Key)=$($environmentEntry.Value)"
        )
    }
    $dockerArgs += @($definition.Image, "sh", "-lc", $testCommand)

    Write-Host "Running '$Name' in $($definition.Image)"
    & docker @dockerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Suite '$Name' failed with exit code $LASTEXITCODE."
    }
}

function Invoke-Suite([string]$Name) {
    if ($Runtime -eq "docker") {
        Invoke-DockerSuite $Name
    }
    else {
        Invoke-LocalSuite $Name
    }
}

if ($Suite -eq "list") {
    Show-Suites
    exit 0
}

if ($Coverage -and $Suite -notin @("deterministic", "all")) {
    Write-Warning "Coverage is most useful for the deterministic suite."
}

if ($Suite -eq "all") {
    foreach ($suiteName in @("deterministic", "agent-regression", "radiology")) {
        Invoke-Suite $suiteName
    }
}
else {
    Invoke-Suite $Suite
}
