from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import func, select

from material_allocation_copilot.application import (
    AllocationPlanInput,
    AllocationService,
    ApprovalDecision,
    ApprovalInput,
    ApprovalService,
    ConfirmedRequirementInput,
    InventoryImportInput,
    InventoryService,
    PlanTerminalStatus,
    ProjectService,
    RequirementService,
    ReservationCreateInput,
    ReservationReleaseInput,
    ReservationService,
    ReservationStatus,
    RuleEvaluationInput,
    TaskCancelInput,
    TaskRetryInput,
    TaskService,
    TaskStatus,
)
from material_allocation_copilot.application.records import ProjectRecord, ReservationRecord
from material_allocation_copilot.application.services import RecoveryService
from material_allocation_copilot.domain import (
    AssessmentMethod,
    AvailabilityStatus,
    DefectDataStatus,
    MaterialItem,
    OrientationRequirement,
    RuleOutcome,
    SlotRequirement,
    StructuralUseStatus,
)
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.persistence import (
    SqlAlchemyUnitOfWork,
    create_db_engine,
    create_session_factory,
)
from material_allocation_copilot.persistence.models import (
    AllocationPlanRow,
    AllocationTaskRow,
    ApprovalRow,
    Base,
    InventoryVersionRow,
    MaterialItemRow,
    ReservationRow,
    ReservedMaterialRow,
    RuleEvaluationRow,
)
from material_allocation_copilot.rules import AllocationAssignment, LengthMinimumRule

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def make_material(**overrides: object) -> MaterialItem:
    values: dict[str, object] = {
        "material_id": "MAT-001",
        "inventory_version_id": "INV-001",
        "source_system": "test_scanner",
        "source_record_id": "SCAN-001",
        "source_file_sha256": HASH_A,
        "source_row_sha256": HASH_B,
        "measured_at": datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
        "length_mm": "4000",
        "section_width_mm": "100",
        "section_height_mm": "200",
    }
    values.update(overrides)
    return MaterialItem.model_validate(values)


def make_slot(**overrides: object) -> SlotRequirement:
    values: dict[str, object] = {
        "project_id": "PRJ-001",
        "slot_requirement_version_id": "SLOTS-v1",
        "slot_id": "SLOT-B01",
        "slot_role": "beam",
        "clear_span_mm": "3000",
        "end_allowance_mm": "100",
        "min_section_width_mm": "90",
        "min_section_height_mm": "190",
        "allow_section_rotation": False,
        "minimum_strength_grade": "C24",
        "orientation_requirement": OrientationRequirement.FIXED,
        "rule_set_version": "rules-0.1.1",
        "requirement_source_ref": "DWG-S201#B01",
        "confirmed_by": "ENG-01",
        "confirmed_at": datetime(2026, 10, 5, 9, 30, tzinfo=UTC),
        "source_row_sha256": HASH_C,
    }
    values.update(overrides)
    return SlotRequirement.model_validate(values)


@pytest.fixture
def persistence(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'service.db').as_posix()}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    yield engine, session_factory, uow_factory
    engine.dispose()


def seed_inputs(
    uow_factory, *, material: MaterialItem | None = None, slot: SlotRequirement | None = None
) -> tuple[MaterialItem, SlotRequirement]:
    material = material or make_material()
    slot = slot or make_slot()
    ProjectService(uow_factory).create_project(project_id="PRJ-001", name="Pilot frame")
    InventoryService(uow_factory).import_inventory(
        InventoryImportInput(
            project_id="PRJ-001",
            inventory_version_id="INV-001",
            idempotency_key="import-001",
            source_system="test_scanner",
            source_path="scanner/batch-001.csv",
            source_sha256=HASH_A,
            imported_at=datetime(2026, 10, 5, 9, 5, tzinfo=UTC),
            materials=(material,),
        )
    )
    RequirementService(uow_factory).save_confirmed(
        ConfirmedRequirementInput(
            requirement_id="REQ-001",
            project_id="PRJ-001",
            idempotency_key="requirement-001",
            slots=(slot,),
        )
    )
    return material, slot


def create_task(uow_factory, *, task_id: str = "TASK-001", key: str = "task-001"):
    return TaskService(uow_factory).create_task(
        allocation_task_id=task_id,
        project_id="PRJ-001",
        inventory_version_id="INV-001",
        requirement_id="REQ-001",
        idempotency_key=key,
    )


def make_plan(
    material: MaterialItem,
    slot: SlotRequirement,
    *,
    uow_factory,
    plan_id: str = "PLAN-001",
    terminal_status: PlanTerminalStatus = PlanTerminalStatus.REQUIRES_REVIEW,
) -> AllocationPlanInput:
    result = LengthMinimumRule().evaluate(material, slot)
    return AllocationPlanInput(
        allocation_plan_id=plan_id,
        allocation_task_id="TASK-001",
        plan_version=1,
        terminal_status=terminal_status,
        input_snapshot_hash=AllocationService(uow_factory).get_input_snapshot_hash("TASK-001"),
        assignments=(AllocationAssignment(slot_id=slot.slot_id, material_id=material.material_id),),
        rule_evaluations=(
            RuleEvaluationInput(
                material_id=material.material_id,
                slot_id=slot.slot_id,
                result=result,
            ),
        ),
    )


def test_complete_workflow_persists_separate_task_plan_rule_and_approval_states(
    persistence,
) -> None:
    engine, _, uow_factory = persistence
    material, slot = seed_inputs(uow_factory)
    task = create_task(uow_factory)
    assert task.status is TaskStatus.PENDING

    plan = AllocationService(uow_factory).save_plan(
        make_plan(material, slot, uow_factory=uow_factory)
    )
    approval_request = ApprovalInput(
        approval_id="APR-001",
        allocation_plan_id=plan.allocation_plan_id,
        idempotency_key="approval-001",
        decision=ApprovalDecision.APPROVED,
        decided_by="ENG-01",
        decided_at=datetime(2026, 10, 5, 10, 0, tzinfo=UTC),
        note="Reviewed against drawing S201.",
    )
    approval_service = ApprovalService(uow_factory)
    approval = approval_service.record_decision(approval_request)
    assert approval_service.record_decision(approval_request) == approval
    with pytest.raises(ApplicationError) as captured:
        approval_service.record_decision(
            approval_request.model_copy(update={"note": "Changed after submission."})
        )
    assert captured.value.code is ErrorCode.CONFLICT

    with engine.connect() as connection:
        task_state = connection.execute(
            select(AllocationTaskRow.status, AllocationTaskRow.completed_at)
        ).one()
        plan_status = connection.scalar(select(AllocationPlanRow.terminal_status))
        evaluation_outcome = connection.scalar(select(RuleEvaluationRow.outcome))
        approval_decision = connection.scalar(select(ApprovalRow.decision))

    assert task_state.status == TaskStatus.COMPLETED.value
    assert task_state.completed_at is not None
    assert plan_status == PlanTerminalStatus.REQUIRES_REVIEW.value
    assert evaluation_outcome == RuleOutcome.PASS.value
    assert approval_decision == ApprovalDecision.APPROVED.value
    assert approval.decision is ApprovalDecision.APPROVED


def test_inventory_retry_is_idempotent_and_conflicting_retry_preserves_history(
    persistence,
) -> None:
    engine, _, uow_factory = persistence
    material, _ = seed_inputs(uow_factory)
    request = InventoryImportInput(
        project_id="PRJ-001",
        inventory_version_id="INV-001",
        idempotency_key="import-001",
        source_system="test_scanner",
        source_path="scanner/batch-001.csv",
        source_sha256=HASH_A,
        imported_at=datetime(2026, 10, 5, 9, 5, tzinfo=UTC),
        materials=(material,),
    )
    service = InventoryService(uow_factory)

    assert service.import_inventory(request).inventory_version_id == "INV-001"
    conflicting = request.model_copy(update={"materials": (make_material(length_mm="4100"),)})
    with pytest.raises(ApplicationError) as captured:
        service.import_inventory(conflicting)

    assert captured.value.code is ErrorCode.CONFLICT
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(InventoryVersionRow)) == 1
        assert connection.scalar(select(func.count()).select_from(MaterialItemRow)) == 1


def test_plan_version_conflict_does_not_overwrite_existing_plan(persistence) -> None:
    engine, _, uow_factory = persistence
    material, slot = seed_inputs(uow_factory)
    create_task(uow_factory)
    service = AllocationService(uow_factory)
    original = service.save_plan(make_plan(material, slot, uow_factory=uow_factory))

    with pytest.raises(ApplicationError) as captured:
        service.save_plan(make_plan(material, slot, uow_factory=uow_factory, plan_id="PLAN-002"))

    assert captured.value.code is ErrorCode.CONFLICT
    with engine.connect() as connection:
        plan_ids = connection.scalars(select(AllocationPlanRow.allocation_plan_id)).all()
    assert plan_ids == [original.allocation_plan_id]


def test_infeasible_plan_cannot_be_approved(persistence) -> None:
    _, _, uow_factory = persistence
    material, slot = seed_inputs(uow_factory)
    create_task(uow_factory)
    plan = AllocationService(uow_factory).save_plan(
        make_plan(
            material, slot, uow_factory=uow_factory, terminal_status=PlanTerminalStatus.INFEASIBLE
        )
    )

    with pytest.raises(ApplicationError) as captured:
        ApprovalService(uow_factory).record_decision(
            ApprovalInput(
                approval_id="APR-001",
                allocation_plan_id=plan.allocation_plan_id,
                idempotency_key="approval-001",
                decision=ApprovalDecision.APPROVED,
                decided_by="ENG-01",
                decided_at=datetime(2026, 10, 5, 10, 0, tzinfo=UTC),
            )
        )

    assert captured.value.code is ErrorCode.CONFLICT


def test_unit_of_work_rolls_back_uncommitted_changes(persistence) -> None:
    _, _, uow_factory = persistence
    with pytest.raises(RuntimeError, match="abort transaction"), uow_factory() as uow:
        uow.projects.add(
            ProjectRecord(
                project_id="PRJ-ROLLBACK",
                name="Must not persist",
                created_at=datetime(2026, 10, 5, 11, 0, tzinfo=UTC),
            )
        )
        raise RuntimeError("abort transaction")

    with uow_factory() as uow:
        assert uow.projects.get("PRJ-ROLLBACK") is None


def test_task_cancel_retry_and_restart_recovery_are_bounded(persistence) -> None:
    _, _, uow_factory = persistence
    seed_inputs(uow_factory)
    original = create_task(uow_factory)
    service = TaskService(uow_factory)

    cancelled = service.cancel(
        TaskCancelInput(
            allocation_task_id=original.allocation_task_id, reason="Operator stopped it"
        )
    )
    assert cancelled.status is TaskStatus.CANCELLED
    assert (
        service.cancel(
            TaskCancelInput(
                allocation_task_id=original.allocation_task_id, reason="Operator stopped it"
            )
        )
        == cancelled
    )

    retry = service.retry(
        TaskRetryInput(
            allocation_task_id="TASK-002",
            retry_of_task_id=original.allocation_task_id,
            idempotency_key="task-retry-002",
        )
    )
    assert retry.attempt == 2
    assert retry.retry_of_task_id == original.allocation_task_id
    with uow_factory() as uow:
        uow.tasks.update_status(retry.allocation_task_id, TaskStatus.RUNNING)
        uow.commit()

    assert RecoveryService(uow_factory).recover_interrupted() == {
        "tasks_failed": 1,
        "agent_runs_failed": 0,
    }
    with uow_factory() as uow:
        recovered = uow.tasks.get(retry.allocation_task_id)
    assert recovered is not None
    assert recovered.status is TaskStatus.FAILED
    assert recovered.failure_code == "interrupted_by_restart"


def test_reservation_conflict_release_and_idempotency(persistence) -> None:
    engine, _, uow_factory = persistence
    material, slot = seed_inputs(uow_factory)
    create_task(uow_factory)
    first_plan = AllocationService(uow_factory).save_plan(
        make_plan(material, slot, uow_factory=uow_factory)
    )
    ApprovalService(uow_factory).record_decision(
        ApprovalInput(
            approval_id="APR-001",
            allocation_plan_id=first_plan.allocation_plan_id,
            idempotency_key="approval-001",
            decision=ApprovalDecision.APPROVED,
            decided_by="ENG-01",
            decided_at=datetime(2026, 10, 5, 10, 0, tzinfo=UTC),
        )
    )
    create_task(uow_factory, task_id="TASK-002", key="task-002")
    second_plan_input = make_plan(
        material, slot, uow_factory=uow_factory, plan_id="PLAN-002"
    ).model_copy(
        update={
            "allocation_task_id": "TASK-002",
            "input_snapshot_hash": AllocationService(uow_factory).get_input_snapshot_hash(
                "TASK-002"
            ),
        }
    )
    second_plan = AllocationService(uow_factory).save_plan(second_plan_input)
    ApprovalService(uow_factory).record_decision(
        ApprovalInput(
            approval_id="APR-002",
            allocation_plan_id=second_plan.allocation_plan_id,
            idempotency_key="approval-002",
            decision=ApprovalDecision.APPROVED,
            decided_by="ENG-01",
            decided_at=datetime(2026, 10, 5, 10, 1, tzinfo=UTC),
        )
    )

    first_request = ReservationCreateInput(
        reservation_id="RSV-001",
        allocation_plan_id=first_plan.allocation_plan_id,
        idempotency_key="reserve-001",
        reserved_by="ENG-01",
    )
    second_request = ReservationCreateInput(
        reservation_id="RSV-002",
        allocation_plan_id=second_plan.allocation_plan_id,
        idempotency_key="reserve-002",
        reserved_by="ENG-01",
    )
    barrier = Barrier(2)

    def attempt(request: ReservationCreateInput) -> ReservationRecord | ErrorCode:
        barrier.wait()
        try:
            return ReservationService(uow_factory).reserve(request)
        except ApplicationError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(attempt, (first_request, second_request)))
    winners = tuple(item for item in outcomes if isinstance(item, ReservationRecord))
    assert len(winners) == 1
    assert outcomes.count(ErrorCode.CONFLICT) == 1
    winner = winners[0]
    winner_request = first_request if winner.reservation_id == "RSV-001" else second_request
    loser_request = second_request if winner.reservation_id == "RSV-001" else first_request

    service = ReservationService(uow_factory)
    assert service.reserve(winner_request) == winner
    released = service.release(
        ReservationReleaseInput(
            reservation_id=winner.reservation_id,
            idempotency_key="release-winner",
            released_by="ENG-01",
        )
    )
    assert released.status is ReservationStatus.RELEASED
    assert (
        service.release(
            ReservationReleaseInput(
                reservation_id=winner.reservation_id,
                idempotency_key="release-winner",
                released_by="ENG-01",
            )
        )
        == released
    )
    assert service.reserve(loser_request).status is ReservationStatus.ACTIVE
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ReservationRow)) == 2
        assert (
            connection.scalar(
                select(func.count())
                .select_from(ReservedMaterialRow)
                .where(ReservedMaterialRow.status == ReservationStatus.ACTIVE.value)
            )
            == 1
        )


@pytest.mark.parametrize("project_id", ["", "spaces in ID", "a" * 129])
def test_project_service_rejects_invalid_identifiers(persistence, project_id: str) -> None:
    _, _, uow_factory = persistence
    with pytest.raises(ApplicationError) as captured:
        ProjectService(uow_factory).create_project(project_id=project_id, name="Frame")
    assert captured.value.code is ErrorCode.INPUT_INVALID


@pytest.mark.parametrize(
    "changes",
    [{"project_id": "PRJ-002"}, {"source_path": "another/source.csv"}],
)
def test_inventory_idempotency_checks_project_and_source_metadata(persistence, changes) -> None:
    _, _, uow_factory = persistence
    seed_inputs(uow_factory)
    ProjectService(uow_factory).create_project(project_id="PRJ-002", name="Other project")
    request = InventoryImportInput(
        project_id="PRJ-001",
        inventory_version_id="INV-001",
        idempotency_key="import-001",
        source_system="test_scanner",
        source_path="scanner/batch-001.csv",
        source_sha256=HASH_A,
        imported_at=datetime(2026, 10, 5, 9, 5, tzinfo=UTC),
        materials=(make_material(),),
    ).model_copy(update=changes)
    with pytest.raises(ApplicationError) as captured:
        InventoryService(uow_factory).import_inventory(request)
    assert captured.value.code is ErrorCode.CONFLICT


def test_service_revalidates_copied_nested_domain_models(persistence) -> None:
    _, _, uow_factory = persistence
    seed_inputs(uow_factory)
    request = InventoryImportInput(
        project_id="PRJ-001",
        inventory_version_id="INV-002",
        idempotency_key="import-002",
        source_system="test_scanner",
        source_path="scanner/batch-002.csv",
        source_sha256=HASH_A,
        imported_at=datetime(2026, 10, 5, 9, 5, tzinfo=UTC),
        materials=(make_material(inventory_version_id="INV-002"),),
    )
    corrupted_material = request.materials[0].model_copy(update={"length_mm": -1})
    corrupted_request = request.model_copy(update={"materials": (corrupted_material,)})
    with pytest.raises(ApplicationError) as captured:
        InventoryService(uow_factory).import_inventory(corrupted_request)
    assert captured.value.code is ErrorCode.INPUT_INVALID
    with uow_factory() as uow:
        assert uow.inventories.get_version("INV-002") is None


@pytest.mark.parametrize(
    "changes",
    [
        {"assignments": (AllocationAssignment(slot_id="SLOT-OTHER", material_id="MAT-001"),)},
        {"assignments": (AllocationAssignment(slot_id="SLOT-B01", material_id="MAT-OTHER"),)},
        {"input_snapshot_hash": HASH_C},
        {"terminal_status": PlanTerminalStatus.COMPLETE_FEASIBLE},
    ],
)
def test_plan_save_rejects_wrong_inputs_and_unverified_feasibility(persistence, changes) -> None:
    engine, _, uow_factory = persistence
    material, slot = seed_inputs(uow_factory)
    create_task(uow_factory)
    request = make_plan(material, slot, uow_factory=uow_factory).model_copy(update=changes)
    with pytest.raises(ApplicationError):
        AllocationService(uow_factory).save_plan(request)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(AllocationPlanRow)) == 0
        assert connection.scalar(select(AllocationTaskRow.status)) == TaskStatus.PENDING.value


def test_plan_save_rejects_fabricated_rule_evidence(persistence) -> None:
    _, _, uow_factory = persistence
    material, slot = seed_inputs(uow_factory)
    create_task(uow_factory)
    request = make_plan(material, slot, uow_factory=uow_factory)
    false_result = request.rule_evaluations[0].result.model_copy(
        update={"actual_value": "invented"}
    )
    request = request.model_copy(
        update={
            "rule_evaluations": (
                RuleEvaluationInput(
                    material_id=material.material_id, slot_id=slot.slot_id, result=false_result
                ),
            )
        }
    )
    with pytest.raises(ApplicationError) as captured:
        AllocationService(uow_factory).save_plan(request)
    assert captured.value.code is ErrorCode.INPUT_INVALID


def test_plan_recheck_saves_missing_rules_and_remains_idempotent(persistence) -> None:
    engine, _, uow_factory = persistence
    material, slot = seed_inputs(uow_factory)
    create_task(uow_factory)
    request = make_plan(material, slot, uow_factory=uow_factory)
    service = AllocationService(uow_factory)
    original = service.save_plan(request)
    assert service.save_plan(request) == original
    assert len(original.payload["rule_evaluations"]) == 6
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(RuleEvaluationRow)) == 6


@pytest.mark.parametrize(
    "material",
    [
        make_material(length_mm="1000"),
        make_material(availability_status=AvailabilityStatus.RESERVED),
    ],
)
def test_reviewable_plan_cannot_hide_a_blocking_failure(persistence, material) -> None:
    _, _, uow_factory = persistence
    material, slot = seed_inputs(uow_factory, material=material)
    create_task(uow_factory)
    with pytest.raises(ApplicationError) as captured:
        AllocationService(uow_factory).save_plan(make_plan(material, slot, uow_factory=uow_factory))
    assert captured.value.code is ErrorCode.INPUT_INVALID


def test_complete_feasible_plan_requires_and_accepts_full_evidence(persistence) -> None:
    _, _, uow_factory = persistence
    material = make_material(
        structural_use_status=StructuralUseStatus.ELIGIBLE,
        strength_grade="C24",
        assessment_method=AssessmentMethod.CERTIFIED_VISUAL_GRADING,
        assessor_id="GRADER-01",
        assessment_date=date(2026, 10, 3),
        assessment_evidence_ref="REPORT-001",
        defect_data_status=DefectDataStatus.COMPLETE,
        availability_status=AvailabilityStatus.AVAILABLE,
    )
    material, slot = seed_inputs(uow_factory, material=material)
    create_task(uow_factory)
    plan = AllocationService(uow_factory).save_plan(
        make_plan(
            material,
            slot,
            uow_factory=uow_factory,
            terminal_status=PlanTerminalStatus.COMPLETE_FEASIBLE,
        )
    )
    assert plan.terminal_status is PlanTerminalStatus.COMPLETE_FEASIBLE
    assert all(item["result"]["outcome"] == "pass" for item in plan.payload["rule_evaluations"])


def test_database_constraint_conflict_rolls_back_flushed_writes(persistence) -> None:
    engine, session_factory, uow_factory = persistence
    seed_inputs(uow_factory)
    with pytest.raises(ApplicationError) as captured, uow_factory() as uow:
        uow.projects.add(ProjectRecord("PRJ-NEW", "Rollback", datetime.now(UTC)))
        uow.projects.add(ProjectRecord("PRJ-001", "Duplicate", datetime.now(UTC)))
        uow.commit()
    assert captured.value.code is ErrorCode.CONFLICT
    with uow_factory() as uow:
        assert uow.projects.get("PRJ-NEW") is None
    with session_factory() as session:
        assert not session.in_transaction()
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(InventoryVersionRow)) == 1


def test_json_rule_evidence_survives_decimal_serialization(persistence) -> None:
    _, _, uow_factory = persistence
    material, slot = seed_inputs(uow_factory)
    create_task(uow_factory)
    request = make_plan(material, slot, uow_factory=uow_factory)
    from_json = AllocationPlanInput.model_validate_json(request.model_dump_json())
    assert AllocationService(uow_factory).save_plan(from_json).allocation_plan_id == "PLAN-001"


@pytest.mark.parametrize("case", ["duplicate_slot", "duplicate_material", "missing_slot"])
def test_plan_recheck_rejects_duplicate_and_missing_assignments(persistence, case: str) -> None:
    _, _, uow_factory = persistence
    material, slot = seed_inputs(uow_factory)
    create_task(uow_factory)
    request = make_plan(material, slot, uow_factory=uow_factory)
    if case == "duplicate_slot":
        changes = {"assignments": request.assignments * 2}
    elif case == "duplicate_material":
        # A distinct slot in a second requirement must still not reuse the same physical item.
        second = make_slot(slot_id="SLOT-B02")
        RequirementService(uow_factory).save_confirmed(
            ConfirmedRequirementInput(
                requirement_id="REQ-002",
                project_id="PRJ-001",
                idempotency_key="requirement-002",
                slots=(slot, second),
            )
        )
        TaskService(uow_factory).create_task(
            allocation_task_id="TASK-002",
            project_id="PRJ-001",
            inventory_version_id="INV-001",
            requirement_id="REQ-002",
            idempotency_key="task-002",
        )
        changes = {
            "allocation_task_id": "TASK-002",
            "input_snapshot_hash": AllocationService(uow_factory).get_input_snapshot_hash(
                "TASK-002"
            ),
            "assignments": request.assignments
            + (AllocationAssignment(slot_id=second.slot_id, material_id=material.material_id),),
        }
    else:
        changes = {
            "assignments": (),
            "rule_evaluations": (),
            "terminal_status": PlanTerminalStatus.COMPLETE_FEASIBLE,
        }
    with pytest.raises(ApplicationError) as captured:
        AllocationService(uow_factory).save_plan(request.model_copy(update=changes))
    assert captured.value.code is ErrorCode.INPUT_INVALID


def test_confirmed_requirement_does_not_discard_mixed_confirmation_metadata(persistence) -> None:
    _, _, uow_factory = persistence
    seed_inputs(uow_factory)
    with pytest.raises(ApplicationError) as captured:
        request = ConfirmedRequirementInput(
            requirement_id="REQ-002",
            project_id="PRJ-001",
            idempotency_key="requirement-002",
            slots=(make_slot(), make_slot(slot_id="SLOT-B02")),
        ).model_copy(
            update={
                "slots": (
                    make_slot(),
                    make_slot(slot_id="SLOT-B02", confirmed_by="ENG-OTHER"),
                )
            }
        )
        RequirementService(uow_factory).save_confirmed(request)
    assert captured.value.code is ErrorCode.INPUT_INVALID


def test_changed_idempotency_key_cannot_be_silently_used_as_an_unrecorded_alias(
    persistence,
) -> None:
    _, _, uow_factory = persistence
    seed_inputs(uow_factory)
    request = ConfirmedRequirementInput(
        requirement_id="REQ-001",
        project_id="PRJ-001",
        idempotency_key="different-key",
        slots=(make_slot(),),
    )
    with pytest.raises(ApplicationError) as captured:
        RequirementService(uow_factory).save_confirmed(request)
    assert captured.value.code is ErrorCode.CONFLICT


@pytest.mark.parametrize(
    "field",
    [
        "max_moisture_content_pct",
        "treatment_restriction_profile_id",
        "defect_restriction_profile_id",
        "capacity_requirement_ref",
    ],
)
def test_complete_plan_cannot_skip_a_declared_unimplemented_constraint(persistence, field) -> None:
    _, _, uow_factory = persistence
    material = make_material(
        structural_use_status=StructuralUseStatus.ELIGIBLE,
        strength_grade="C24",
        assessment_method=AssessmentMethod.CERTIFIED_VISUAL_GRADING,
        assessor_id="GRADER-01",
        assessment_date=date(2026, 10, 3),
        assessment_evidence_ref="REPORT-001",
        defect_data_status=DefectDataStatus.COMPLETE,
        availability_status=AvailabilityStatus.AVAILABLE,
    )
    value = "20" if field == "max_moisture_content_pct" else "PROFILE-001"
    slot = make_slot(**{field: value})
    material, slot = seed_inputs(uow_factory, material=material, slot=slot)
    create_task(uow_factory)
    with pytest.raises(ApplicationError) as captured:
        AllocationService(uow_factory).save_plan(
            make_plan(
                material,
                slot,
                uow_factory=uow_factory,
                terminal_status=PlanTerminalStatus.COMPLETE_FEASIBLE,
            )
        )
    assert captured.value.code is ErrorCode.INPUT_INVALID
