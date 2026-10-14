[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$database = "material_allocation"
$restoreDatabase = "material_allocation_restore_drill"

function Assert-CommandSucceeded([string] $Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

docker compose exec -T postgres sh -c "pg_dump -U material_allocation -d $database -Fc -f /tmp/material_allocation.dump"
Assert-CommandSucceeded "pg_dump"
docker compose exec -T postgres dropdb -U material_allocation --if-exists $restoreDatabase
Assert-CommandSucceeded "drop previous drill database"
docker compose exec -T postgres createdb -U material_allocation $restoreDatabase
Assert-CommandSucceeded "create drill database"
docker compose exec -T postgres pg_restore -U material_allocation -d $restoreDatabase --exit-on-error /tmp/material_allocation.dump
Assert-CommandSucceeded "pg_restore"
$revision = docker compose exec -T postgres psql -U material_allocation -d $restoreDatabase -Atc "SELECT version_num FROM alembic_version"
Assert-CommandSucceeded "verify restored revision"
if ($revision.Trim() -ne "20261011_0006") {
    throw "Restored schema revision is unexpected: $revision"
}
docker compose exec -T postgres psql -U material_allocation -d $restoreDatabase -Atc "SELECT count(*) FROM projects"
Assert-CommandSucceeded "read restored business table"
docker compose exec -T postgres dropdb -U material_allocation $restoreDatabase
Assert-CommandSucceeded "drop drill database"
Write-Host "PostgreSQL backup and restore verification passed."
