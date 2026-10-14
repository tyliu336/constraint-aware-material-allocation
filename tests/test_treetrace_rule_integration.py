from datetime import UTC, datetime
from pathlib import Path

import pytest

from material_allocation_copilot.adapters import import_treetrace_boards
from material_allocation_copilot.application import (
    InventoryImportInput,
    InventoryService,
    ProjectService,
)
from material_allocation_copilot.domain import (
    OrientationRequirement,
    RuleOutcome,
    SlotRequirement,
    StructuralUseStatus,
)
from material_allocation_copilot.persistence import (
    SqlAlchemyUnitOfWork,
    create_db_engine,
    create_session_factory,
)
from material_allocation_copilot.persistence.models import Base
from material_allocation_copilot.rules import default_rule_registry

LOCAL_BOARDS_PATH = (
    Path(__file__).resolve().parents[1] / ".local-data" / "TreeTrace" / "tables" / "boards.txt"
)
LEGACY_BOARDS_PATH = Path("D:/timber-match/data/raw/TreeTrace/tables/boards.txt")
REAL_BOARDS_PATH = LOCAL_BOARDS_PATH if LOCAL_BOARDS_PATH.exists() else LEGACY_BOARDS_PATH


@pytest.mark.skipif(not REAL_BOARDS_PATH.exists(), reason="local TreeTrace V1 source unavailable")
def test_real_treetrace_materials_flow_through_pair_rule_registry() -> None:
    imported = import_treetrace_boards(
        REAL_BOARDS_PATH,
        inventory_version_id="INV-treetrace-v1-rule-test",
    )
    slot = SlotRequirement(
        project_id="PRJ-test",
        slot_requirement_version_id="SLOTS-test-v1",
        slot_id="SLOT-test-beam",
        slot_role="beam",
        clear_span_mm="3000",
        end_allowance_mm="100",
        min_section_width_mm="44",
        min_section_height_mm="128",
        allow_section_rotation=False,
        minimum_strength_grade="C18",
        orientation_requirement=OrientationRequirement.FIXED,
        rule_set_version="rules-0.1.1",
        requirement_source_ref="TEST-FIXTURE",
        confirmed_by="ENG-test",
        confirmed_at=datetime(2026, 10, 4, 10, 0, tzinfo=UTC),
        source_row_sha256="a" * 64,
    )
    registry = default_rule_registry()

    grade_evaluable = next(
        item
        for item in imported.materials
        if item.structural_use_status is not StructuralUseStatus.INELIGIBLE
    )
    rejected = next(
        item
        for item in imported.materials
        if item.structural_use_status is StructuralUseStatus.INELIGIBLE
    )
    evaluable_results = {
        result.rule_id: result for result in registry.evaluate_pair(grade_evaluable, slot)
    }
    rejected_results = {result.rule_id: result for result in registry.evaluate_pair(rejected, slot)}

    assert evaluable_results["hard.length.minimum"].outcome is RuleOutcome.PASS
    assert evaluable_results["hard.section.minimum"].outcome is RuleOutcome.PASS
    assert (
        evaluable_results["hard.structural_evidence.required"].outcome is RuleOutcome.NOT_EVALUABLE
    )
    assert evaluable_results["hard.grade.minimum"].outcome is RuleOutcome.PASS
    assert evaluable_results["hard.defect.midspan"].outcome is RuleOutcome.NOT_EVALUABLE
    assert rejected_results["hard.grade.minimum"].outcome is RuleOutcome.FAIL


@pytest.mark.skipif(not REAL_BOARDS_PATH.exists(), reason="local TreeTrace V1 source unavailable")
def test_real_treetrace_inventory_flows_through_application_service(tmp_path: Path) -> None:
    imported = import_treetrace_boards(
        REAL_BOARDS_PATH,
        inventory_version_id="INV-treetrace-v1-service-test",
        imported_at=datetime(2026, 10, 5, 8, 0, tzinfo=UTC),
    )
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'treetrace.db').as_posix()}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    ProjectService(uow_factory).create_project(
        project_id="PRJ-treetrace-service-test",
        name="TreeTrace service integration",
    )
    record = InventoryService(uow_factory).import_inventory(
        InventoryImportInput.from_treetrace(
            imported,
            project_id="PRJ-treetrace-service-test",
            idempotency_key="import-treetrace-v1-service-test",
        )
    )

    assert record.item_count == 346
    assert record.source_sha256 == imported.source_sha256
    with uow_factory() as uow:
        assert uow.inventories.count_materials(record.inventory_version_id) == 346
    engine.dispose()
