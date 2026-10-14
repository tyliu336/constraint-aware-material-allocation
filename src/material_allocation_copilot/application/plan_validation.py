"""Recheck persisted task inputs before accepting an externally supplied plan."""

from material_allocation_copilot.application.contracts import (
    AllocationPlanInput,
    PlanTerminalStatus,
    RuleEvaluationInput,
)
from material_allocation_copilot.domain import (
    AvailabilityStatus,
    MaterialItem,
    RuleOutcome,
    RuleSeverity,
    SlotRequirement,
)
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.rules import UniqueMaterialUseRule, default_rule_registry
from material_allocation_copilot.rules.coverage import unevaluated_slot_fields


def verify_plan(
    request: AllocationPlanInput,
    materials: tuple[MaterialItem, ...],
    slots: tuple[SlotRequirement, ...],
) -> tuple[RuleEvaluationInput, ...]:
    registry = default_rule_registry()
    materials_by_id = {item.material_id: item for item in materials}
    slots_by_id = {slot.slot_id: slot for slot in slots}
    if any(slot.rule_set_version != registry.rule_set_version for slot in slots):
        raise ApplicationError(
            ErrorCode.INPUT_INVALID,
            "The requirement references an unsupported rule set version.",
            context={"supported_version": registry.rule_set_version},
        )

    seen_slots: set[str] = set()
    evaluations: list[RuleEvaluationInput] = []
    for assignment in request.assignments:
        if assignment.slot_id not in slots_by_id or assignment.material_id not in materials_by_id:
            raise ApplicationError(
                ErrorCode.INPUT_INVALID,
                "Plan assignment does not belong to the task inventory and requirement.",
                context={"slot_id": assignment.slot_id, "material_id": assignment.material_id},
            )
        if assignment.slot_id in seen_slots:
            raise ApplicationError(ErrorCode.INPUT_INVALID, "A slot is assigned more than once.")
        seen_slots.add(assignment.slot_id)
        evaluations.extend(
            RuleEvaluationInput(
                material_id=assignment.material_id,
                slot_id=assignment.slot_id,
                result=result,
            )
            for result in registry.evaluate_pair(
                materials_by_id[assignment.material_id], slots_by_id[assignment.slot_id]
            )
        )

    unavailable = frozenset(
        item.material_id
        for item in materials
        if item.availability_status
        in {
            AvailabilityStatus.RESERVED,
            AvailabilityStatus.ALLOCATED,
            AvailabilityStatus.UNAVAILABLE,
        }
    )
    evaluations.append(
        RuleEvaluationInput(
            result=UniqueMaterialUseRule().evaluate(
                request.assignments, unavailable_material_ids=unavailable
            )
        )
    )

    # Submitted evidence can be a subset, but cannot contradict the authoritative recheck.
    authoritative = {
        (item.material_id, item.slot_id, item.result.rule_id, item.result.rule_version): item
        for item in evaluations
    }
    seen_evidence: set[tuple[str | None, str | None, str, str]] = set()
    for item in request.rule_evaluations:
        key = (item.material_id, item.slot_id, item.result.rule_id, item.result.rule_version)
        expected = authoritative.get(key)
        if (
            key in seen_evidence
            or expected is None
            or item.model_dump(mode="json") != expected.model_dump(mode="json")
        ):
            raise ApplicationError(
                ErrorCode.INPUT_INVALID,
                "Submitted rule evidence does not match the deterministic recheck.",
                context={"rule_id": item.result.rule_id},
            )
        seen_evidence.add(key)

    blocking = [
        item.result for item in evaluations if item.result.severity is RuleSeverity.BLOCKING
    ]
    reviewable = request.terminal_status in {
        PlanTerminalStatus.COMPLETE_FEASIBLE,
        PlanTerminalStatus.REQUIRES_REVIEW,
    }
    if reviewable and any(result.outcome is RuleOutcome.FAIL for result in blocking):
        raise ApplicationError(
            ErrorCode.INPUT_INVALID,
            "A plan with a blocking rule failure cannot be labelled feasible or reviewable.",
        )
    if request.terminal_status is PlanTerminalStatus.COMPLETE_FEASIBLE:
        unchecked = any(unevaluated_slot_fields(slot) for slot in slots)
        unknown_availability = any(
            materials_by_id[item.material_id].availability_status is AvailabilityStatus.UNKNOWN
            for item in request.assignments
        )
        if (
            seen_slots != set(slots_by_id)
            or any(result.outcome is not RuleOutcome.PASS for result in blocking)
            or unchecked
            or unknown_availability
        ):
            raise ApplicationError(
                ErrorCode.INPUT_INVALID,
                "Complete feasibility requires full slot coverage and evaluated hard constraints.",
            )
    return tuple(evaluations)
