[CmdletBinding()]
param(
    [string] $ProjectId = "PRJ-demo",
    [string] $UserId = "USER-demo",
    [int] $Port = 8000,
    [switch] $ValidateOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Development environment not found. Run .\scripts\bootstrap-dev.ps1 first."
}
if ($ProjectId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
    throw "ProjectId must use 1-128 letters, digits, dots, underscores, or hyphens."
}
if ($UserId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
    throw "UserId must use 1-128 letters, digits, dots, underscores, or hyphens."
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535."
}
$hasApiKey = -not [string]::IsNullOrWhiteSpace($env:MAC_OPENAI_API_KEY) -or `
    -not [string]::IsNullOrWhiteSpace($env:MAC_OPENAI_API_KEY_FILE)
if (-not $ValidateOnly -and -not $hasApiKey) {
    throw "MAC_OPENAI_API_KEY or MAC_OPENAI_API_KEY_FILE is required before starting the demo."
}
if (-not $ValidateOnly -and [string]::IsNullOrWhiteSpace($env:MAC_REQUIREMENT_MODEL)) {
    throw "MAC_REQUIREMENT_MODEL is required. Set the model ID in this PowerShell session before starting the demo."
}

Push-Location $projectRoot
try {
    & $venvPython -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Database migration failed."
    }

    $credentialJson = & $venvPython -m material_allocation_copilot.api.credentials `
        --user $UserId `
        --project $ProjectId `
        --role planner `
        --role reviewer
    if ($LASTEXITCODE -ne 0) {
        throw "Demo credential generation failed."
    }

    $credential = $credentialJson | ConvertFrom-Json
    $principalJson = ConvertTo-Json -InputObject @($credential.credential_entry) -Depth 10 -Compress

    Write-Host ""
    Write-Host "Local demo identity"
    Write-Host "  Project: $ProjectId"
    Write-Host "  User:    $UserId"
    Write-Host "  Token:   $($credential.bearer_token)"
    Write-Host ""

    if ($ValidateOnly) {
        Write-Host "Demo bootstrap validation passed; the API was not started."
        return
    }

    $previousPrincipals = $env:MAC_API_PRINCIPALS_JSON
    $env:MAC_API_PRINCIPALS_JSON = $principalJson
    try {
        Write-Host "Open http://127.0.0.1:$Port/ and paste the token into Local connection."
        Write-Host "Step 3 uses the configured OpenAI model to interpret the prompt."
        Write-Host "Press Ctrl+C to stop the API. The token is not written to disk."
        & $venvPython -m uvicorn material_allocation_copilot.api.app:create_app `
            --factory `
            --host 127.0.0.1 `
            --port $Port
        if ($LASTEXITCODE -ne 0) {
            throw "Local API exited with code $LASTEXITCODE."
        }
    } finally {
        if ($null -eq $previousPrincipals) {
            Remove-Item Env:MAC_API_PRINCIPALS_JSON -ErrorAction SilentlyContinue
        } else {
            $env:MAC_API_PRINCIPALS_JSON = $previousPrincipals
        }
    }
} finally {
    Pop-Location
}
