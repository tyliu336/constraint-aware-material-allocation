"""Stable input contracts for application services."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from material_allocation_copilot.adapters import TreeTraceImportResult
from material_allocation_copilot.domain import (
    MaterialItem,
    OrientationRequirement,
    RuleResult,
    SlotRequirement,
    VisibleSurfaceScope,
)
from material_allocation_copilot.domain.types import Identifier, Sha256Hex
from material_allocation_copilot.rules import AllocationAssignment


class _ApplicationInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


def _timezone_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return value


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReservationStatus(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"


class PlanTerminalStatus(StrEnum):
    COMPLETE_FEASIBLE = "complete_feasible"
    REQUIRES_REVIEW = "requires_review"
    INFEASIBLE = "infeasible"
    INPUT_INVALID = "input_invalid"
    BUDGET_EXHAUSTED = "budget_exhausted"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class AgentRunStatus(StrEnum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCESS = "success"
    NEEDS_CLARIFICATION = "needs_clarification"
    NO_SOLUTION = "no_solution"
    TOOL_ERROR = "tool_error"
    BUDGET_EXHAUSTED = "budget_exhausted"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"


class AgentApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class AgentActionType(StrEnum):
    SAVE_ALLOCATION_PLAN = "save_allocation_plan"
    RESERVE_INVENTORY = "reserve_inventory"
    APPROVE_PLAN = "approve_plan"


class ProjectCreateInput(_ApplicationInput):
    project_id: Identifier
    name: Annotated[str, StringConstraints(min_length=1, max_length=300)]


class TaskCreateInput(_ApplicationInput):
    allocation_task_id: Identifier
    project_id: Identifier
    inventory_version_id: Identifier
    requirement_id: Identifier
    idempotency_key: Identifier


class TaskCancelInput(_ApplicationInput):
    allocation_task_id: Identifier
    reason: Annotated[str, StringConstraints(min_length=1, max_length=1000)]


class TaskRetryInput(_ApplicationInput):
    allocation_task_id: Identifier
    retry_of_task_id: Identifier
    idempotency_key: Identifier


class ReservationCreateInput(_ApplicationInput):
    reservation_id: Identifier
    allocation_plan_id: Identifier
    idempotency_key: Identifier
    reserved_by: Identifier


class ReservationReleaseInput(_ApplicationInput):
    reservation_id: Identifier
    idempotency_key: Identifier
    released_by: Identifier


class InventoryImportInput(_ApplicationInput):
    project_id: Identifier
    inventory_version_id: Identifier
    idempotency_key: Identifier
    source_system: Identifier
    source_path: str = Field(min_length=1)
    source_sha256: Sha256Hex
    imported_at: datetime
    materials: tuple[MaterialItem, ...] = Field(min_length=1)

    @field_validator("imported_at")
    @classmethod
    def imported_at_must_have_timezone(cls, value: datetime) -> datetime:
        return _timezone_aware(value, "imported_at")

    @model_validator(mode="after")
    def material_versions_must_match(self) -> InventoryImportInput:
        mismatched = sorted(
            material.material_id
            for material in self.materials
            if material.inventory_version_id != self.inventory_version_id
        )
        if mismatched:
            raise ValueError(
                "all materials must reference inventory_version_id; mismatched="
                + ",".join(mismatched[:5])
            )
        material_ids = [material.material_id for material in self.materials]
        if len(material_ids) != len(set(material_ids)):
            raise ValueError("material_id values must be unique within an inventory version")
        if any(
            material.source_system != self.source_system
            or material.source_file_sha256 != self.source_sha256
            for material in self.materials
        ):
            raise ValueError("material source system and file hash must match the import source")
        return self

    @classmethod
    def from_treetrace(
        cls,
        result: TreeTraceImportResult,
        *,
        project_id: str,
        idempotency_key: str,
    ) -> InventoryImportInput:
        return cls(
            project_id=project_id,
            inventory_version_id=result.inventory_version_id,
            idempotency_key=idempotency_key,
            source_system="treetrace_douglas",
            source_path=str(Path(result.source_path)),
            source_sha256=result.source_sha256,
            imported_at=result.imported_at,
            materials=result.materials,
        )


class ConfirmedSoftPreference(_ApplicationInput):
    preference_type: Literal[
        "minimize_cut_waste",
        "preserve_scarce_long_stock",
        "minimize_not_evaluable",
        "minimize_surface_defects",
    ]
    weight: Decimal = Field(gt=0, le=1000, max_digits=10, decimal_places=6)
    scope: Annotated[str, StringConstraints(min_length=1, max_length=4000)] | None = None
    rationale: Annotated[str, StringConstraints(min_length=1, max_length=4000)]


class ConfirmedRequirementInput(_ApplicationInput):
    requirement_id: Identifier
    project_id: Identifier
    idempotency_key: Identifier
    slots: tuple[SlotRequirement, ...] = Field(min_length=1)
    soft_preferences: tuple[ConfirmedSoftPreference, ...] = Field(default=(), max_length=3)

    @model_validator(mode="after")
    def slots_must_form_one_versioned_set(self) -> ConfirmedRequirementInput:
        if any(slot.project_id != self.project_id for slot in self.slots):
            raise ValueError("all slots must belong to project_id")
        versions = {slot.slot_requirement_version_id for slot in self.slots}
        if len(versions) != 1:
            raise ValueError("all slots must share one slot_requirement_version_id")
        slot_ids = [slot.slot_id for slot in self.slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("slot_id values must be unique within a requirement")
        if len({slot.rule_set_version for slot in self.slots}) != 1:
            raise ValueError("all slots must share one rule_set_version")
        if len({(slot.confirmed_by, slot.confirmed_at) for slot in self.slots}) != 1:
            raise ValueError("all slots must share one confirmation identity and timestamp")
        preference_types = [item.preference_type for item in self.soft_preferences]
        if len(preference_types) != len(set(preference_types)):
            raise ValueError("confirmed soft preference types must be unique")
        return self


class RequirementDraftCreateInput(_ApplicationInput):
    draft_id: Identifier
    project_id: Identifier
    idempotency_key: Identifier
    natural_language: Annotated[str, StringConstraints(min_length=1, max_length=12000)]


class PreferenceInterpretationInput(_ApplicationInput):
    project_id: Identifier
    prompt: Annotated[str, StringConstraints(min_length=1, max_length=4000)]


class ClarificationAnswer(_ApplicationInput):
    question_id: Identifier
    answer: Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class RequirementDraftClarifyInput(_ApplicationInput):
    draft_id: Identifier
    project_id: Identifier
    idempotency_key: Identifier
    answers: tuple[ClarificationAnswer, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def question_ids_are_unique(self) -> RequirementDraftClarifyInput:
        question_ids = [item.question_id for item in self.answers]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("clarification question_id values must be unique")
        return self


class DraftConfirmationSlot(_ApplicationInput):
    slot_id: Identifier
    slot_role: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    clear_span_mm: Decimal | None = Field(default=None, gt=0)
    end_allowance_mm: Decimal = Field(ge=0)
    min_section_width_mm: Decimal = Field(gt=0)
    min_section_height_mm: Decimal = Field(gt=0)
    allow_section_rotation: bool
    minimum_strength_grade: (
        Annotated[str, StringConstraints(min_length=1, max_length=500)] | None
    ) = None
    capacity_requirement_ref: (
        Annotated[str, StringConstraints(min_length=1, max_length=500)] | None
    ) = None
    orientation_requirement: OrientationRequirement
    defect_restriction_profile_id: Identifier | None = None
    max_moisture_content_pct: Decimal | None = Field(default=None, ge=0, le=100)
    treatment_restriction_profile_id: Identifier | None = None
    visible_surface_scope: VisibleSurfaceScope = VisibleSurfaceScope.NONE
    aesthetic_requirement_text: Annotated[str, StringConstraints(max_length=2000)] | None = None
    visual_reference_ref: Annotated[str, StringConstraints(max_length=500)] | None = None
    rule_set_version: Identifier
    requirement_source_ref: Annotated[str, StringConstraints(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def structural_basis_is_required(self) -> DraftConfirmationSlot:
        if self.minimum_strength_grade is None and self.capacity_requirement_ref is None:
            raise ValueError("minimum_strength_grade or capacity_requirement_ref is required")
        return self


class RequirementDraftConfirmInput(_ApplicationInput):
    draft_id: Identifier
    requirement_id: Identifier
    project_id: Identifier
    idempotency_key: Identifier
    slot_requirement_version_id: Identifier
    slots: tuple[DraftConfirmationSlot, ...] = Field(min_length=1, max_length=1000)
    soft_preferences: tuple[ConfirmedSoftPreference, ...] = Field(default=(), max_length=3)

    @model_validator(mode="after")
    def slot_ids_are_unique(self) -> RequirementDraftConfirmInput:
        slot_ids = [item.slot_id for item in self.slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("slot_id values must be unique within a confirmed draft")
        preference_types = [item.preference_type for item in self.soft_preferences]
        if len(preference_types) != len(set(preference_types)):
            raise ValueError("confirmed soft preference types must be unique")
        return self


class RuleEvaluationInput(_ApplicationInput):
    material_id: Identifier | None = None
    slot_id: Identifier | None = None
    result: RuleResult


class AllocationPlanInput(_ApplicationInput):
    allocation_plan_id: Identifier
    allocation_task_id: Identifier
    plan_version: int = Field(ge=1)
    terminal_status: PlanTerminalStatus
    input_snapshot_hash: Sha256Hex
    assignments: tuple[AllocationAssignment, ...]
    rule_evaluations: tuple[RuleEvaluationInput, ...]


class ApprovalInput(_ApplicationInput):
    approval_id: Identifier
    allocation_plan_id: Identifier
    idempotency_key: Identifier
    decision: ApprovalDecision
    decided_by: Identifier
    decided_at: datetime
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("decided_at")
    @classmethod
    def decided_at_must_have_timezone(cls, value: datetime) -> datetime:
        return _timezone_aware(value, "decided_at")
