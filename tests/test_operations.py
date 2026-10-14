from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from material_allocation_copilot.operations import backup_sqlite, verify_sqlite_backup


def test_sqlite_backup_is_restored_and_verified(tmp_path: Path, monkeypatch) -> None:
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    restored = tmp_path / "restored.db"
    monkeypatch.setenv("MAC_ENVIRONMENT", "test")
    monkeypatch.setenv("MAC_TEST_DATABASE_URL", f"sqlite:///{source.as_posix()}")
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{source.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (project_id, name, created_at) "
                "VALUES ('RECOVERY-TEST', 'sentinel', :created_at)"
            ),
            {"created_at": datetime.now(UTC).isoformat()},
        )
    engine.dispose()

    digest = backup_sqlite(source, backup)
    report = verify_sqlite_backup(backup, restored)

    assert report["sha256"] == digest
    assert report["integrity_check"] == "ok"
    assert report["schema_revision"] == "20261011_0006"
    assert report["project_rows"] == 1
