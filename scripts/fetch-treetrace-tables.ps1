[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dataRoot = Join-Path $projectRoot ".local-data\TreeTrace"
$tablesRoot = Join-Path $dataRoot "tables"
$boardsPath = Join-Path $tablesRoot "boards.txt"
$archivePath = Join-Path $dataRoot "tables.zip"

$downloadUri = "https://entrepot.recherche.data.gouv.fr/api/access/datafile/149304"
$expectedArchiveMd5 = "6d53d68645d3fa1815c6e846a2a8759a"
$expectedBoardsSha256 = "6e8050e7bf8310136448c39f56c800fc7ddebd21199d8d421f8fd964da2c9121"
$expectedRows = 346

function Get-NormalizedHash {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [ValidateSet("MD5", "SHA256")] [string] $Algorithm
    )
    return (Get-FileHash -LiteralPath $Path -Algorithm $Algorithm).Hash.ToLowerInvariant()
}

if (Test-Path -LiteralPath $boardsPath) {
    $existingHash = Get-NormalizedHash -Path $boardsPath -Algorithm "SHA256"
    if ($existingHash -ne $expectedBoardsSha256) {
        throw "Existing boards.txt does not match the audited TreeTrace V1 hash."
    }
    Write-Host "TreeTrace V1 tables are already present and verified."
    exit 0
}

New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null

if (-not (Test-Path -LiteralPath $archivePath)) {
    Invoke-RestMethod -Uri $downloadUri -OutFile $archivePath
}

$archiveHash = Get-NormalizedHash -Path $archivePath -Algorithm "MD5"
if ($archiveHash -ne $expectedArchiveMd5) {
    throw "Downloaded tables.zip does not match the official MD5."
}

Expand-Archive -LiteralPath $archivePath -DestinationPath $dataRoot -Force

if (-not (Test-Path -LiteralPath $boardsPath)) {
    throw "The verified archive did not contain tables/boards.txt."
}

$boardsHash = Get-NormalizedHash -Path $boardsPath -Algorithm "SHA256"
$rowCount = (Get-Content -LiteralPath $boardsPath | Measure-Object -Line).Lines - 1
if ($boardsHash -ne $expectedBoardsSha256 -or $rowCount -ne $expectedRows) {
    throw "Extracted boards.txt does not match the audited TreeTrace V1 identity."
}

Get-ChildItem -LiteralPath $tablesRoot -File | ForEach-Object { $_.IsReadOnly = $true }
Write-Host "TreeTrace V1 restored: $rowCount rows, SHA-256 $boardsHash"
