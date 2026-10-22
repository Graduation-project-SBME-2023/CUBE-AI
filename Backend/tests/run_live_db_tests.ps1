<#
.SYNOPSIS
    Runs the live database suites against a throwaway PostgreSQL 18.

.DESCRIPTION
    Starts a disposable PostgreSQL container on a private Docker network, runs
    the five live suites in the project image, and removes the container and the
    network afterwards, whatever happens. The compose database is never touched.

      - test_v1_o_renames_live.py           rename migration up/down
      - test_v1_p_session_graph_live.py     session-graph migration: backfills, rules, downgrade refusals
      - test_v1_t_agent_transcripts_live.py agent-transcript migration up/down and its refusals
      - test_v1_p_transactions_live.py      harness transactions through the HTTP API, fencing, ORM == schema
      - test_v1_p_graph_store_live.py       the GraphStore scenario table on HttpGraphStore

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File Backend/tests/run_live_db_tests.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File Backend/tests/run_live_db_tests.ps1 -Pattern "transactions"
#>
param(
    # Substring of the suite file names to run (default: all five).
    [string]$Pattern = "",
    [string]$Image = "cubeai1/agents-app:latest",
    [string]$PostgresImage = "postgres:18-alpine"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Suffix = [guid]::NewGuid().ToString("N").Substring(0, 8)
$Network = "cubeai-livedb-$Suffix"
$Database = "cubeai-livedb-pg-$Suffix"

$Suites = @(
    "tests/integration/test_v1_o_renames_live.py",
    "tests/integration/test_v1_p_session_graph_live.py",
    "tests/integration/test_v1_t_agent_transcripts_live.py",
    "tests/integration/test_v1_p_transactions_live.py",
    "tests/integration/test_v1_p_graph_store_live.py"
) | Where-Object { $_ -like "*$Pattern*" }
if (-not $Suites) { throw "No live suite matches '$Pattern'." }

# The role is the compose one: the first migration grants to it. The migration
# suites drive alembic synchronously; the transaction suites use asyncpg. Same
# database, two URL schemes.
$SyncUrl = "postgresql://workflow_user:cubeai@${Database}:5432/cubeai_test"
$AsyncUrl = "postgresql+asyncpg://workflow_user:cubeai@${Database}:5432/cubeai_test"

try {
    & docker network create $Network | Out-Null
    & docker run -d --name $Database --network $Network `
        -e POSTGRES_USER=workflow_user -e POSTGRES_PASSWORD=cubeai -e POSTGRES_DB=cubeai_test $PostgresImage | Out-Null
    $ready = $false
    foreach ($i in 1..30) {
        & docker exec $Database pg_isready -U workflow_user -d cubeai_test *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "PostgreSQL did not become ready." }

    $migrationSuites = ($Suites | Where-Object { $_ -match "renames|session_graph|agent_transcripts" }) -join " "
    $apiSuites = ($Suites | Where-Object { $_ -match "transactions|graph_store" }) -join " "
    $commands = @("python -m pip install --quiet -r requirements/test.txt psycopg2-binary 'psycopg[binary]'")
    if ($migrationSuites) {
        $commands += "V1_LIVE_DATABASE_URL=$SyncUrl python -m pytest -c pytest.ini $migrationSuites -p no:cacheprovider -q"
    }
    if ($apiSuites) {
        $commands += "V1_LIVE_DATABASE_URL=$AsyncUrl python -m pytest -c pytest.ini $apiSuites -p no:cacheprovider -q"
    }

    & docker run --rm --network $Network -v "${RepoRoot}:/workspace" -w /workspace/Backend `
        $Image sh -lc ($commands -join " && ")
    if ($LASTEXITCODE -ne 0) { throw "Live database suites failed (exit code $LASTEXITCODE)." }
    Write-Host "Live database suites passed."
}
finally {
    & docker rm -f $Database *> $null
    & docker network rm $Network *> $null
}
