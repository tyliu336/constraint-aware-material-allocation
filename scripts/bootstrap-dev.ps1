[CmdletBinding()]
param(
    [string] $PythonVersion = "3.13",
    [switch] $SkipTreeTrace
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

Push-Location $projectRoot
try {
    $venvWorks = $false
    if (Test-Path -LiteralPath $venvPython) {
        & $venvPython --version *> $null
        $venvWorks = $LASTEXITCODE -eq 0
    }

    if (-not $venvWorks) {
        & py "-$PythonVersion" -m venv --clear .venv
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create .venv with Python $PythonVersion."
        }
    }

    & $venvPython -m pip install --upgrade pip setuptools wheel
    & $venvPython -m pip install -e ".[dev,pilot]"
    & $venvPython -m pip check

    if (-not $SkipTreeTrace) {
        & (Join-Path $PSScriptRoot "fetch-treetrace-tables.ps1")
    }

    Write-Host "Development environment is ready."
    Write-Host "Activate with: .\.venv\Scripts\Activate.ps1"
} finally {
    Pop-Location
}
