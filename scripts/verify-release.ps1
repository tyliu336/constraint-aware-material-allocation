[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$boards = Join-Path $projectRoot ".local-data\TreeTrace\tables\boards.txt"
$verificationRoot = Join-Path $projectRoot ".pytest-verification-release"

function Assert-CommandSucceeded([string] $Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing .venv. Run scripts/bootstrap-dev.ps1 first."
}
if (-not (Test-Path -LiteralPath $boards)) {
    throw "Missing audited TreeTrace V1 fixture. Run scripts/fetch-treetrace-tables.ps1 first."
}

New-Item -ItemType Directory -Force -Path $verificationRoot | Out-Null

Push-Location $projectRoot
try {
    & $python -m pip check
    Assert-CommandSucceeded "pip check"
    & $python -m pytest -ra --basetemp $verificationRoot
    Assert-CommandSucceeded "pytest"
    & $python -m material_allocation_copilot.evals.runner
    Assert-CommandSucceeded "offline eval"
    & $python -m ruff check .
    Assert-CommandSucceeded "ruff check"
    & $python -m ruff format --check .
    Assert-CommandSucceeded "ruff format check"

    $env:MAC_ENVIRONMENT = "test"
    $env:MAC_TEST_DATABASE_URL = "sqlite:///./.pytest-verification-release/readiness.db"
    & $python -m alembic upgrade head
    Assert-CommandSucceeded "first alembic upgrade"
    & $python -m alembic upgrade head
    Assert-CommandSucceeded "second alembic upgrade"
    & $python -m alembic check
    Assert-CommandSucceeded "alembic check"
    & $python .\scripts\verify-backup-restore.py
    Assert-CommandSucceeded "backup restore drill"
    & $python -c "from importlib.metadata import version; from material_allocation_copilot.api.app import create_app; app=create_app(); paths=len(app.openapi()['paths']); assert app.version == '1.4.0'; assert paths == 41; assert version('psycopg').startswith('3.'); assert version('mcp'); print({'version': app.version, 'openapi_paths': paths, 'psycopg': version('psycopg'), 'mcp': version('mcp')})"
    Assert-CommandSucceeded "release contract check"

    Write-Host "Release verification passed."
} finally {
    Pop-Location
}
