"""Create a migrated SQLite fixture, back it up, restore it, and verify the restore."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from material_allocation_copilot.operations import backup_sqlite, verify_sqlite_backup

ROOT = Path(__file__).resolve().parents[1]
DRILL_ROOT = ROOT / ".local-data" / "recovery-drill"


def main() -> None:
    DRILL_ROOT.mkdir(parents=True, exist_ok=True)
    source = DRILL_ROOT / "source.db"
    backup = DRILL_ROOT / "backup.db"
    restored = DRILL_ROOT / "restored.db"
    for target in (source, backup, restored, backup.with_suffix(".db.sha256")):
        if target.exists():
            target.unlink()

    os.environ["MAC_ENVIRONMENT"] = "test"
    os.environ["MAC_TEST_DATABASE_URL"] = f"sqlite:///{source.as_posix()}"
    alembic = Config(str(ROOT / "alembic.ini"))
    command.upgrade(alembic, "head")
    engine = create_engine(os.environ["MAC_TEST_DATABASE_URL"])
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (project_id, name, created_at) "
                "VALUES (:project_id, :name, :created_at)"
            ),
            {
                "project_id": "RECOVERY-SENTINEL",
                "name": "Recovery drill sentinel",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )
    engine.dispose()

    digest = backup_sqlite(source, backup)
    report = verify_sqlite_backup(backup, restored)
    if report["project_rows"] != 1 or report["sha256"] != digest:
        raise RuntimeError("recovery drill did not preserve the sentinel project")
    print(json.dumps(report, sort_keys=True))
    print("SQLite backup and restore verification passed.")


if __name__ == "__main__":
    main()
