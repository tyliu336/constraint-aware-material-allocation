from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from material_allocation_copilot.persistence.models import Base


def test_initial_migration_upgrades_empty_database_and_is_repeatable(
    tmp_path: Path, monkeypatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "migration.db"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    monkeypatch.setenv("MAC_ENVIRONMENT", "test")
    monkeypatch.setenv("MAC_TEST_DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.delenv("MAC_DEVELOPMENT_DATABASE_URL", raising=False)
    monkeypatch.delenv("MAC_PILOT_DATABASE_URL", raising=False)

    command.upgrade(config, "head")
    command.upgrade(config, "head")
    command.check(config)

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    expected = {
        "alembic_version",
        "projects",
        "inventory_versions",
        "material_items",
        "requirements",
        "confirmed_requirement_preferences",
        "requirement_drafts",
        "requirement_draft_revisions",
        "context_snapshots",
        "tool_call_logs",
        "slot_requirements",
        "allocation_tasks",
        "allocation_plans",
        "rule_evaluations",
        "approvals",
        "agent_runs",
        "agent_traces",
        "agent_approval_requests",
        "inventory_reservations",
        "reserved_materials",
    }
    assert table_names == expected
    for table_name, table in Base.metadata.tables.items():
        migrated_columns = {column["name"] for column in inspector.get_columns(table_name)}
        assert migrated_columns == set(table.columns.keys())
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert revision == "20261011_0006"
    engine.dispose()
