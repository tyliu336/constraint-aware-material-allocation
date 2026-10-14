"""Transaction-scoped application services; no ORM types cross this boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError

from material_allocation_copilot.application.contracts import (
    AllocationPlanInput,
    ApprovalDecision,
    ApprovalInput,
    ConfirmedRequirementInput,
    InventoryImportInput,
    PlanTerminalStatus,
    ProjectCreateInput,
    ReservationCreateInput,
    ReservationReleaseInput,
    ReservationStatus,
    TaskCancelInput,
    TaskCreateInput,
    TaskRetryInput,
    TaskStatus,
)
from material_allocation_copilot.application.hashing import canonical_hash, task_input_snapshot_hash
from material_allocation_copilot.application.plan_validation import verify_plan
from material_allocation_copilot.application.ports import UnitOfWork
from material_allocation_copilot.application.records import (
    AllocationPlanRecord,
    AllocationTaskRecord,
    ApprovalRecord,
    InventoryVersionRecord,
    MaterialSnapshotRecord,
    ProjectRecord,
    RequirementRecord,
    ReservationRecord,
    RuleEvaluationRecord,
)
from material_allocation_copilot.domain import MaterialItem, SlotRequirement
from material_allocation_copilot.errors import ApplicationError, ErrorCode

UnitOfWorkFactory = Callable[[], UnitOfWork]
MAX_TASK_ATTEMPTS = 3


def validate_input[InputModel: BaseModel](model: type[InputModel], value: object) -> InputModel:
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise ApplicationError(
            ErrorCode.INPUT_INVALID,
            "Application input does not satisfy its contract.",
            context={
                "errors": error.errors(
                    include_input=False, include_url=False, include_context=False
                )
            },
        ) from error


def _not_found(resource_name: str, resource_id: str) -> ApplicationError:
    return ApplicationError(
        ErrorCode.NOT_FOUND,
        f"{resource_name} was not found.",
        context={"resource_id": resource_id},
    )


def _conflict(message: str, **context: object) -> ApplicationError:
    return ApplicationError(ErrorCode.CONFLICT, message, context=context)


class ProjectService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def create_project(self, *, project_id: str, name: str) -> ProjectRecord:
        request = validate_input(ProjectCreateInput, {"project_id": project_id, "name": name})
        project_id = request.project_id
        clean_name = request.name

        with self._uow_factory() as uow:
            existing = uow.projects.get(project_id)
            if existing is not None:
                if existing.name == clean_name:
                    return existing
                raise _conflict(
                    "Project ID already exists with different content.",
                    project_id=project_id,
                )

            record = ProjectRecord(
                project_id=project_id,
                name=clean_name,
                created_at=datetime.now(UTC),
            )
            uow.projects.add(record)
            uow.commit()
            return record


class InventoryService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def import_inventory(self, request: InventoryImportInput) -> InventoryVersionRecord:
        request = validate_input(InventoryImportInput, request)
        material_payloads = tuple(
            material.model_dump(mode="json")
            for material in sorted(request.materials, key=lambda item: item.material_id)
        )
        snapshot_hash = canonical_hash(material_payloads)

        def matches(existing: InventoryVersionRecord) -> bool:
            return (
                existing.project_id == request.project_id
                and existing.inventory_version_id == request.inventory_version_id
                and existing.source_system == request.source_system
                and existing.source_path == request.source_path
                and existing.source_sha256 == request.source_sha256
                and existing.snapshot_hash == snapshot_hash
            )

        with self._uow_factory() as uow:
            if uow.projects.get(request.project_id) is None:
                raise _not_found("Project", request.project_id)

            existing_by_key = uow.inventories.get_by_idempotency_key(request.idempotency_key)
            if existing_by_key is not None:
                if matches(existing_by_key):
                    return existing_by_key
                raise _conflict(
                    "Inventory idempotency key was already used for different content.",
                    idempotency_key=request.idempotency_key,
                )

            existing_version = uow.inventories.get_version(request.inventory_version_id)
            if existing_version is not None:
                raise _conflict(
                    "Inventory version ID already exists; retry with its original idempotency key.",
                    inventory_version_id=request.inventory_version_id,
                )

            record = InventoryVersionRecord(
                inventory_version_id=request.inventory_version_id,
                project_id=request.project_id,
                idempotency_key=request.idempotency_key,
                source_system=request.source_system,
                source_path=request.source_path,
                source_sha256=request.source_sha256,
                snapshot_hash=snapshot_hash,
                item_count=len(request.materials),
                imported_at=request.imported_at,
            )
            materials = tuple(
                MaterialSnapshotRecord(
                    inventory_version_id=request.inventory_version_id,
                    material_id=payload["material_id"],
                    source_row_sha256=payload["source_row_sha256"],
                    payload=payload,
                )
                for payload in material_payloads
            )
            uow.inventories.add_version(record)
            uow.inventories.add_materials(materials)
            uow.commit()
            return record


class RequirementService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def save_confirmed(self, request: ConfirmedRequirementInput) -> RequirementRecord:
        request = validate_input(ConfirmedRequirementInput, request)
        slot_payloads = tuple(
            slot.model_dump(mode="json")
            for slot in sorted(request.slots, key=lambda item: item.slot_id)
        )
        preference_payloads = tuple(
            item.model_dump(mode="json")
            for item in sorted(request.soft_preferences, key=lambda item: item.preference_type)
        )
        content_hash = canonical_hash(
            {"slots": slot_payloads, "soft_preferences": preference_payloads}
        )
        first_slot = request.slots[0]

        with self._uow_factory() as uow:
            if uow.projects.get(request.project_id) is None:
                raise _not_found("Project", request.project_id)

            existing_by_key = uow.requirements.get_by_idempotency_key(request.idempotency_key)
            if existing_by_key is not None:
                if (
                    existing_by_key.requirement_id == request.requirement_id
                    and existing_by_key.content_hash == content_hash
                ):
                    return existing_by_key
                raise _conflict(
                    "Requirement idempotency key was already used for different content.",
                    idempotency_key=request.idempotency_key,
                )

            existing = uow.requirements.get(request.requirement_id)
            if existing is not None:
                raise _conflict(
                    "Requirement ID already exists; retry with its original idempotency key.",
                    requirement_id=request.requirement_id,
                )

            record = RequirementRecord(
                requirement_id=request.requirement_id,
                project_id=request.project_id,
                idempotency_key=request.idempotency_key,
                slot_requirement_version_id=first_slot.slot_requirement_version_id,
                content_hash=content_hash,
                slots=slot_payloads,
                confirmed_by=first_slot.confirmed_by,
                confirmed_at=first_slot.confirmed_at,
                soft_preferences=preference_payloads,
            )
            uow.requirements.add(record)
            uow.commit()
            return record


class TaskService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def create_task(
        self,
        *,
        allocation_task_id: str,
        project_id: str,
        inventory_version_id: str,
        requirement_id: str,
        idempotency_key: str,
    ) -> AllocationTaskRecord:
        request = validate_input(
            TaskCreateInput,
            {
                "allocation_task_id": allocation_task_id,
                "project_id": project_id,
                "inventory_version_id": inventory_version_id,
                "requirement_id": requirement_id,
                "idempotency_key": idempotency_key,
            },
        )
        allocation_task_id = request.allocation_task_id
        project_id = request.project_id
        inventory_version_id = request.inventory_version_id
        requirement_id = request.requirement_id
        idempotency_key = request.idempotency_key
        with self._uow_factory() as uow:
            project = uow.projects.get(project_id)
            inventory = uow.inventories.get_version(inventory_version_id)
            requirement = uow.requirements.get(requirement_id)
            if project is None:
                raise _not_found("Project", project_id)
            if inventory is None:
                raise _not_found("Inventory version", inventory_version_id)
            if requirement is None:
                raise _not_found("Requirement", requirement_id)
            if inventory.project_id != project_id or requirement.project_id != project_id:
                raise _conflict(
                    "Task inputs do not belong to the same project.",
                    project_id=project_id,
                )

            existing_by_key = uow.tasks.get_by_idempotency_key(idempotency_key)
            expected_identity = (
                allocation_task_id,
                project_id,
                inventory_version_id,
                requirement_id,
            )
            if existing_by_key is not None:
                actual_identity = (
                    existing_by_key.allocation_task_id,
                    existing_by_key.project_id,
                    existing_by_key.inventory_version_id,
                    existing_by_key.requirement_id,
                )
                if actual_identity == expected_identity:
                    return existing_by_key
                raise _conflict(
                    "Task idempotency key was already used for different inputs.",
                    idempotency_key=idempotency_key,
                )

            existing = uow.tasks.get(allocation_task_id)
            if existing is not None:
                raise _conflict(
                    "Allocation task ID already exists.",
                    allocation_task_id=allocation_task_id,
                )

            created_at = datetime.now(UTC)
            record = AllocationTaskRecord(
                allocation_task_id=allocation_task_id,
                project_id=project_id,
                inventory_version_id=inventory_version_id,
                requirement_id=requirement_id,
                idempotency_key=idempotency_key,
                status=TaskStatus.PENDING,
                created_at=created_at,
                updated_at=created_at,
            )
            uow.tasks.add(record)
            uow.commit()
            return record

    def cancel(self, request: TaskCancelInput) -> AllocationTaskRecord:
        request = validate_input(TaskCancelInput, request)
        with self._uow_factory() as uow:
            task = uow.tasks.get(request.allocation_task_id)
            if task is None:
                raise _not_found("Allocation task", request.allocation_task_id)
            if task.status is TaskStatus.CANCELLED:
                if task.cancellation_reason == request.reason:
                    return task
                raise _conflict("Task was already cancelled with a different reason.")
            if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
                raise _conflict(
                    "Only pending or running tasks can be cancelled.",
                    status=task.status.value,
                )
            completed_at = datetime.now(UTC)
            uow.tasks.update_status(
                task.allocation_task_id,
                TaskStatus.CANCELLED,
                completed_at=completed_at,
                cancellation_reason=request.reason,
            )
            uow.commit()
            return replace(
                task,
                status=TaskStatus.CANCELLED,
                completed_at=completed_at,
                updated_at=completed_at,
                cancellation_reason=request.reason,
            )

    def retry(self, request: TaskRetryInput) -> AllocationTaskRecord:
        request = validate_input(TaskRetryInput, request)
        with self._uow_factory() as uow:
            original = uow.tasks.get(request.retry_of_task_id)
            if original is None:
                raise _not_found("Allocation task", request.retry_of_task_id)
            existing = uow.tasks.get_by_idempotency_key(request.idempotency_key)
            if existing is not None:
                if (
                    existing.allocation_task_id == request.allocation_task_id
                    and existing.retry_of_task_id == request.retry_of_task_id
                ):
                    return existing
                raise _conflict(
                    "Task retry idempotency key was already used for different inputs.",
                    idempotency_key=request.idempotency_key,
                )
            if uow.tasks.get(request.allocation_task_id) is not None:
                raise _conflict(
                    "Allocation task ID already exists.",
                    allocation_task_id=request.allocation_task_id,
                )
            if original.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
                raise _conflict(
                    "Only failed or cancelled tasks can be retried.",
                    status=original.status.value,
                )
            if original.attempt >= MAX_TASK_ATTEMPTS:
                raise _conflict(
                    "Task retry limit has been reached.",
                    max_attempts=MAX_TASK_ATTEMPTS,
                )
            created_at = datetime.now(UTC)
            record = AllocationTaskRecord(
                allocation_task_id=request.allocation_task_id,
                project_id=original.project_id,
                inventory_version_id=original.inventory_version_id,
                requirement_id=original.requirement_id,
                idempotency_key=request.idempotency_key,
                status=TaskStatus.PENDING,
                created_at=created_at,
                updated_at=created_at,
                attempt=original.attempt + 1,
                retry_of_task_id=original.allocation_task_id,
            )
            uow.tasks.add(record)
            uow.commit()
            return record


class AllocationService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get_input_snapshot_hash(self, allocation_task_id: str) -> str:
        with self._uow_factory() as uow:
            task = uow.tasks.get(allocation_task_id)
            if task is None:
                raise _not_found("Allocation task", allocation_task_id)
            inventory = uow.inventories.get_version(task.inventory_version_id)
            requirement = uow.requirements.get(task.requirement_id)
            if inventory is None or requirement is None:
                raise _conflict("Task input snapshot is incomplete.")
            return task_input_snapshot_hash(task, inventory, requirement)

    def save_plan(self, request: AllocationPlanInput) -> AllocationPlanRecord:
        request = validate_input(AllocationPlanInput, request)
        with self._uow_factory() as uow:
            task = uow.tasks.get(request.allocation_task_id)
            if task is None:
                raise _not_found("Allocation task", request.allocation_task_id)

            inventory = uow.inventories.get_version(task.inventory_version_id)
            requirement = uow.requirements.get(task.requirement_id)
            if inventory is None or requirement is None:
                raise _conflict("Task input snapshot is incomplete.")
            if request.input_snapshot_hash != task_input_snapshot_hash(
                task, inventory, requirement
            ):
                raise _conflict("Plan input snapshot hash does not match the task inputs.")
            materials = tuple(
                MaterialItem.model_validate(item.payload)
                for item in uow.inventories.list_materials(task.inventory_version_id)
            )
            slots = tuple(SlotRequirement.model_validate(slot) for slot in requirement.slots)
            verified = verify_plan(request, materials, slots)
            payload = request.model_dump(mode="json")
            payload["rule_evaluations"] = [item.model_dump(mode="json") for item in verified]
            content_hash = canonical_hash(payload)

            existing = uow.plans.get(request.allocation_plan_id)
            if existing is not None:
                if existing.content_hash == content_hash:
                    return existing
                raise _conflict(
                    "Allocation plan ID already exists with different content.",
                    allocation_plan_id=request.allocation_plan_id,
                )
            existing_version = uow.plans.get_by_task_version(
                request.allocation_task_id, request.plan_version
            )
            if existing_version is not None:
                raise _conflict(
                    "Plan version already exists for this task.",
                    allocation_task_id=request.allocation_task_id,
                    plan_version=request.plan_version,
                )

            created_at = datetime.now(UTC)
            record = AllocationPlanRecord(
                allocation_plan_id=request.allocation_plan_id,
                allocation_task_id=request.allocation_task_id,
                plan_version=request.plan_version,
                terminal_status=request.terminal_status,
                input_snapshot_hash=request.input_snapshot_hash,
                content_hash=content_hash,
                payload=payload,
                created_at=created_at,
            )
            evaluations = tuple(
                RuleEvaluationRecord(
                    allocation_plan_id=request.allocation_plan_id,
                    material_id=item.material_id,
                    slot_id=item.slot_id,
                    rule_id=item.result.rule_id,
                    rule_version=item.result.rule_version,
                    outcome=item.result.outcome.value,
                    severity=item.result.severity.value,
                    payload=item.result.model_dump(mode="json"),
                )
                for item in verified
            )
            uow.plans.add(record, evaluations)
            uow.tasks.update_status(
                request.allocation_task_id,
                TaskStatus.COMPLETED,
                completed_at=created_at,
            )
            uow.commit()
            return record


class ApprovalService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def record_decision(self, request: ApprovalInput) -> ApprovalRecord:
        request = validate_input(ApprovalInput, request)
        with self._uow_factory() as uow:
            plan = uow.plans.get(request.allocation_plan_id)
            if plan is None:
                raise _not_found("Allocation plan", request.allocation_plan_id)
            if request.decision == ApprovalDecision.APPROVED and plan.terminal_status not in {
                PlanTerminalStatus.COMPLETE_FEASIBLE,
                PlanTerminalStatus.REQUIRES_REVIEW,
            }:
                raise _conflict(
                    "Only feasible or reviewable plans can be approved.",
                    allocation_plan_id=request.allocation_plan_id,
                    terminal_status=plan.terminal_status.value,
                )

            existing_by_key = uow.approvals.get_by_idempotency_key(request.idempotency_key)
            if existing_by_key is not None:
                if (
                    existing_by_key.approval_id == request.approval_id
                    and existing_by_key.allocation_plan_id == request.allocation_plan_id
                    and existing_by_key.decision == request.decision
                    and existing_by_key.decided_by == request.decided_by
                    and existing_by_key.decided_at == request.decided_at
                    and existing_by_key.note == request.note
                ):
                    return existing_by_key
                raise _conflict(
                    "Approval idempotency key was already used for different content.",
                    idempotency_key=request.idempotency_key,
                )

            existing_for_plan = uow.approvals.get_by_plan(request.allocation_plan_id)
            if existing_for_plan is not None:
                raise _conflict(
                    "This plan already has an approval decision.",
                    allocation_plan_id=request.allocation_plan_id,
                )
            if uow.approvals.get(request.approval_id) is not None:
                raise _conflict("Approval ID already exists.", approval_id=request.approval_id)

            record = ApprovalRecord(
                approval_id=request.approval_id,
                allocation_plan_id=request.allocation_plan_id,
                idempotency_key=request.idempotency_key,
                decision=request.decision,
                decided_by=request.decided_by,
                decided_at=request.decided_at,
                note=request.note,
            )
            uow.approvals.add(record)
            uow.commit()
            return record


class ReservationService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def reserve(self, request: ReservationCreateInput) -> ReservationRecord:
        request = validate_input(ReservationCreateInput, request)
        with self._uow_factory() as uow:
            existing = uow.reservations.get_by_idempotency_key(request.idempotency_key)
            if existing is not None:
                if (
                    existing.reservation_id == request.reservation_id
                    and existing.allocation_plan_id == request.allocation_plan_id
                    and existing.reserved_by == request.reserved_by
                ):
                    return existing
                raise _conflict(
                    "Reservation idempotency key was already used for different inputs.",
                    idempotency_key=request.idempotency_key,
                )
            if uow.reservations.get(request.reservation_id) is not None:
                raise _conflict(
                    "Reservation ID already exists.", reservation_id=request.reservation_id
                )
            if uow.reservations.get_by_plan(request.allocation_plan_id) is not None:
                raise _conflict(
                    "This allocation plan already has a reservation.",
                    allocation_plan_id=request.allocation_plan_id,
                )
            plan = uow.plans.get(request.allocation_plan_id)
            if plan is None:
                raise _not_found("Allocation plan", request.allocation_plan_id)
            approval = uow.approvals.get_by_plan(request.allocation_plan_id)
            if approval is None or approval.decision is not ApprovalDecision.APPROVED:
                raise _conflict("An approved plan is required before inventory reservation.")
            task = uow.tasks.get(plan.allocation_task_id)
            if task is None:
                raise _conflict("Allocation plan task is unavailable.")
            material_ids = tuple(
                sorted({item["material_id"] for item in plan.payload.get("assignments", [])})
            )
            if not material_ids:
                raise _conflict("A plan without material assignments cannot be reserved.")
            reserved_at = datetime.now(UTC)
            record = ReservationRecord(
                reservation_id=request.reservation_id,
                project_id=task.project_id,
                allocation_plan_id=plan.allocation_plan_id,
                inventory_version_id=task.inventory_version_id,
                idempotency_key=request.idempotency_key,
                status=ReservationStatus.ACTIVE,
                material_ids=material_ids,
                reserved_by=request.reserved_by,
                reserved_at=reserved_at,
            )
            uow.reservations.add(record)
            uow.commit()
            return record

    def get(self, reservation_id: str) -> ReservationRecord:
        with self._uow_factory() as uow:
            record = uow.reservations.get(reservation_id)
            if record is None:
                raise _not_found("Inventory reservation", reservation_id)
            return record

    def release(self, request: ReservationReleaseInput) -> ReservationRecord:
        request = validate_input(ReservationReleaseInput, request)
        with self._uow_factory() as uow:
            by_key = uow.reservations.get_by_release_idempotency_key(request.idempotency_key)
            if by_key is not None:
                if (
                    by_key.reservation_id == request.reservation_id
                    and by_key.released_by == request.released_by
                ):
                    return by_key
                raise _conflict(
                    "Release idempotency key was already used for a different reservation.",
                    idempotency_key=request.idempotency_key,
                )
            record = uow.reservations.get(request.reservation_id)
            if record is None:
                raise _not_found("Inventory reservation", request.reservation_id)
            if record.status is ReservationStatus.RELEASED:
                raise _conflict("Reservation was already released with a different request.")
            released_at = datetime.now(UTC)
            uow.reservations.release(
                request.reservation_id,
                release_idempotency_key=request.idempotency_key,
                released_by=request.released_by,
                released_at=released_at,
            )
            uow.commit()
            return replace(
                record,
                status=ReservationStatus.RELEASED,
                release_idempotency_key=request.idempotency_key,
                released_by=request.released_by,
                released_at=released_at,
            )


class RecoveryService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def recover_interrupted(self) -> dict[str, int]:
        with self._uow_factory() as uow:
            recovered_at = datetime.now(UTC)
            tasks = uow.tasks.fail_interrupted(recovered_at=recovered_at)
            agent_runs = uow.agent_runs.fail_interrupted(recovered_at=recovered_at)
            uow.commit()
            return {"tasks_failed": tasks, "agent_runs_failed": agent_runs}
