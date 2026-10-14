"""Online SQLite backup plus restore verification for local pilot drills."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text

from material_allocation_copilot.persistence import create_db_engine
from material_allocation_copilot.persistence.bootstrap import verify_schema
from material_allocation_copilot.persistence.models import Base


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_sqlite(source: Path, destination: Path) -> str:
    source = source.resolve(strict=True)
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source == destination:
        raise ValueError("backup destination must differ from the source database")
    with (
        sqlite3.connect(source) as source_connection,
        sqlite3.connect(destination) as destination_connection,
    ):
        source_connection.backup(destination_connection)
    digest = _sha256(destination)
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        f"{digest}  {destination.name}\n", encoding="utf-8"
    )
    return digest


def verify_sqlite_backup(backup: Path, restore_target: Path) -> dict[str, Any]:
    backup = backup.resolve(strict=True)
    restore_target = restore_target.resolve()
    expected_hash = (
        backup.with_suffix(backup.suffix + ".sha256").read_text(encoding="utf-8").split()[0]
    )
    actual_hash = _sha256(backup)
    if actual_hash != expected_hash:
        raise ValueError("backup checksum mismatch")
    restore_target.parent.mkdir(parents=True, exist_ok=True)
    with (
        sqlite3.connect(backup) as backup_connection,
        sqlite3.connect(restore_target) as restore_connection,
    ):
        backup_connection.backup(restore_connection)
        integrity = restore_connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise ValueError(f"restored database integrity check failed: {integrity}")

    engine = create_db_engine(f"sqlite:///{restore_target.as_posix()}")
    try:
        verify_schema(engine)
        restored_tables = set(inspect(engine).get_table_names())
        expected_tables = set(Base.metadata.tables) | {"alembic_version"}
        if restored_tables != expected_tables:
            raise ValueError("restored database table set differs from application metadata")
        with engine.connect() as connection:
            projects = int(connection.scalar(text("SELECT count(*) FROM projects")) or 0)
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        engine.dispose()
    return {
        "sha256": actual_hash,
        "integrity_check": integrity,
        "schema_revision": revision,
        "project_rows": projects,
        "table_count": len(restored_tables),
    }
