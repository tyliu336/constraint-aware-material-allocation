"""Read use cases shared by API and future tools; ORM stays behind repository ports."""

from dataclasses import dataclass
from decimal import Decimal

from material_allocation_copilot.application.access import Principal
from material_allocation_copilot.application.contracts import RuleEvaluationInput
from material_allocation_copilot.application.hashing import task_input_snapshot_hash
from material_allocation_copilot.application.records import (
    AllocationPlanRecord,
    AllocationTaskRecord,
    ApprovalRecord,
    InventoryVersionRecord,
    MaterialSnapshotRecord,
    ProjectRecord,
    RequirementRecord,
    RuleEvaluationRecord,
    ToolCallLogRecord,
)
from material_allocation_copilot.application.services import UnitOfWorkFactory, _not_found
from material_allocation_copilot.domain import MaterialItem, SlotRequirement, StructuralUseStatus
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.rules import default_rule_registry


@dataclass(frozen=True, slots=True)
class Page[Record]:
    items: tuple[Record, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class TaskInputs:
    task: AllocationTaskRecord
    input_snapshot_hash: str
    materials: tuple[MaterialItem, ...]
    slots: tuple[SlotRequirement, ...]


class QueryService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def list_projects(
        self, principal: Principal, *, limit: int, offset: int
    ) -> Page[ProjectRecord]:
        self._pagination(limit, offset)
        with self._uow_factory() as uow:
            items, total = uow.projects.list_page(principal.project_ids, limit=limit, offset=offset)
            return Page(items, total, limit, offset)

    def get_project(self, principal: Principal, project_id: str) -> ProjectRecord:
        principal.require_project(project_id)
        with self._uow_factory() as uow:
            record = uow.projects.get(project_id)
            if record is None:
                raise _not_found("Project", project_id)
            return record

    def get_inventory(
        self, principal: Principal, project_id: str, inventory_id: str
    ) -> InventoryVersionRecord:
        self.get_project(principal, project_id)
        with self._uow_factory() as uow:
            record = uow.inventories.get_version(inventory_id)
            if record is None or record.project_id != project_id:
                raise _not_found("Inventory version", inventory_id)
            return record

    def list_materials(
        self, principal: Principal, project_id: str, inventory_id: str, *, limit: int, offset: int
    ) -> Page[MaterialSnapshotRecord]:
        self._pagination(limit, offset)
        self.get_inventory(principal, project_id, inventory_id)
        with self._uow_factory() as uow:
            items = uow.inventories.materials_page(inventory_id, limit=limit, offset=offset)
            return Page(items, uow.inventories.count_materials(inventory_id), limit, offset)

    def search_inventory(
        self,
        principal: Principal,
        project_id: str,
        inventory_id: str,
        *,
        min_length_mm: Decimal | None = None,
        min_section_width_mm: Decimal | None = None,
        min_section_height_mm: Decimal | None = None,
        strength_grade: str | None = None,
        structural_use_status: StructuralUseStatus | None = None,
        limit: int,
        offset: int,
    ) -> Page[MaterialSnapshotRecord]:
        self._pagination(limit, offset)
        self.get_inventory(principal, project_id, inventory_id)
        with self._uow_factory() as uow:
            records = uow.inventories.list_materials(inventory_id)
        matches = []
        for record in records:
            material = MaterialItem.model_validate(record.payload)
            if min_length_mm is not None and material.length_mm < min_length_mm:
                continue
            if (
                min_section_width_mm is not None
                and material.section_width_mm < min_section_width_mm
            ):
                continue
            if (
                min_section_height_mm is not None
                and material.section_height_mm < min_section_height_mm
            ):
                continue
            if strength_grade is not None and material.strength_grade != strength_grade:
                continue
            if (
                structural_use_status is not None
                and material.structural_use_status is not structural_use_status
            ):
                continue
            matches.append(record)
        return Page(tuple(matches[offset : offset + limit]), len(matches), limit, offset)

    def get_material_details(
        self,
        principal: Principal,
        project_id: str,
        inventory_id: str,
        material_id: str,
    ) -> MaterialSnapshotRecord:
        self.get_inventory(principal, project_id, inventory_id)
        with self._uow_factory() as uow:
            record = uow.inventories.get_material(inventory_id, material_id)
        if record is None:
            raise _not_found("Material", material_id)
        return record

    def get_requirement(
        self, principal: Principal, project_id: str, requirement_id: str
    ) -> RequirementRecord:
        self.get_project(principal, project_id)
        with self._uow_factory() as uow:
            record = uow.requirements.get(requirement_id)
            if record is None or record.project_id != project_id:
                raise _not_found("Requirement", requirement_id)
            return record

    def list_slots(
        self, principal: Principal, project_id: str, requirement_id: str, *, limit: int, offset: int
    ) -> Page[SlotRequirement]:
        self._pagination(limit, offset)
        self.get_requirement(principal, project_id, requirement_id)
        with self._uow_factory() as uow:
            payloads, total = uow.requirements.slots_page(
                requirement_id, limit=limit, offset=offset
            )
            return Page(
                tuple(SlotRequirement.model_validate(item) for item in payloads),
                total,
                limit,
                offset,
            )

    def get_task(
        self, principal: Principal, project_id: str, task_id: str
    ) -> tuple[AllocationTaskRecord, str]:
        self.get_project(principal, project_id)
        with self._uow_factory() as uow:
            record = uow.tasks.get(task_id)
            if record is None or record.project_id != project_id:
                raise _not_found("Allocation task", task_id)
            inventory = uow.inventories.get_version(record.inventory_version_id)
            requirement = uow.requirements.get(record.requirement_id)
            if inventory is None or requirement is None:
                raise ApplicationError(
                    ErrorCode.INTERNAL_ERROR, "Task input snapshot is unavailable."
                )
            return record, task_input_snapshot_hash(record, inventory, requirement)

    def get_plan(
        self, principal: Principal, project_id: str, plan_id: str
    ) -> tuple[AllocationPlanRecord, ApprovalRecord | None]:
        self.get_project(principal, project_id)
        with self._uow_factory() as uow:
            plan = uow.plans.get(plan_id)
            if plan is None:
                raise _not_found("Allocation plan", plan_id)
            task = uow.tasks.get(plan.allocation_task_id)
            if task is None or task.project_id != project_id:
                raise _not_found("Allocation plan", plan_id)
            return plan, uow.approvals.get_by_plan(plan_id)

    def list_plans(
        self,
        principal: Principal,
        project_id: str,
        task_id: str,
        *,
        limit: int,
        offset: int,
    ) -> Page[AllocationPlanRecord]:
        self._pagination(limit, offset)
        self.get_task(principal, project_id, task_id)
        with self._uow_factory() as uow:
            items, total = uow.plans.list_by_task_page(task_id, limit=limit, offset=offset)
            return Page(items, total, limit, offset)

    def list_evaluations(
        self, principal: Principal, project_id: str, plan_id: str, *, limit: int, offset: int
    ) -> Page[RuleEvaluationRecord]:
        self._pagination(limit, offset)
        self.get_plan(principal, project_id, plan_id)
        with self._uow_factory() as uow:
            items, total = uow.plans.evaluations_page(plan_id, limit=limit, offset=offset)
            return Page(items, total, limit, offset)

    def validate_pair(
        self, principal: Principal, project_id: str, task_id: str, material_id: str, slot_id: str
    ) -> tuple[tuple[RuleEvaluationInput, ...], str]:
        task, snapshot_hash = self.get_task(principal, project_id, task_id)
        requirement = self.get_requirement(principal, project_id, task.requirement_id)
        slot = next((item for item in requirement.slots if item["slot_id"] == slot_id), None)
        with self._uow_factory() as uow:
            material = uow.inventories.get_material(task.inventory_version_id, material_id)
        if material is None or slot is None:
            raise _not_found("Material or slot in task inputs", f"{material_id}:{slot_id}")
        results = default_rule_registry().evaluate_pair(
            MaterialItem.model_validate(material.payload), SlotRequirement.model_validate(slot)
        )
        return tuple(
            RuleEvaluationInput(material_id=material_id, slot_id=slot_id, result=result)
            for result in results
        ), snapshot_hash

    def list_tool_run_calls(
        self, principal: Principal, project_id: str, run_id: str
    ) -> tuple[ToolCallLogRecord, ...]:
        """Return one authorized tool-run audit trail for protocol resources."""

        self.get_project(principal, project_id)
        with self._uow_factory() as uow:
            records = uow.tool_calls.list_by_run(run_id)
        if not records or any(record.project_id != project_id for record in records):
            raise _not_found("Tool run", run_id)
        return records

    def load_task_inputs(self, principal: Principal, project_id: str, task_id: str) -> TaskInputs:
        """Load immutable inputs together, never trusting an unrelated parent resource path."""
        principal.require_project(project_id)
        with self._uow_factory() as uow:
            if uow.projects.get(project_id) is None:
                raise _not_found("Project", project_id)
            task = uow.tasks.get(task_id)
            if task is None or task.project_id != project_id:
                raise _not_found("Allocation task", task_id)
            inventory = uow.inventories.get_version(task.inventory_version_id)
            requirement = uow.requirements.get(task.requirement_id)
            if (
                inventory is None
                or requirement is None
                or inventory.project_id != project_id
                or requirement.project_id != project_id
            ):
                raise ApplicationError(ErrorCode.INTERNAL_ERROR, "Task inputs are inconsistent.")
            return TaskInputs(
                task=task,
                input_snapshot_hash=task_input_snapshot_hash(task, inventory, requirement),
                materials=tuple(
                    MaterialItem.model_validate(item.payload)
                    for item in uow.inventories.list_materials(task.inventory_version_id)
                ),
                slots=tuple(SlotRequirement.model_validate(slot) for slot in requirement.slots),
            )

    @staticmethod
    def _pagination(limit: int, offset: int) -> None:
        if not 1 <= limit <= 100 or offset < 0:
            raise ApplicationError(ErrorCode.INPUT_INVALID, "Invalid pagination parameters.")
