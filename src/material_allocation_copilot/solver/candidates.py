"""Evaluate every pair, preserving failures and unknowns separately from usable edges."""

import hashlib
import json
from collections import Counter

from material_allocation_copilot.domain import (
    AvailabilityStatus,
    MaterialItem,
    RuleEvidence,
    RuleOutcome,
    RuleResult,
    RuleScope,
    RuleSeverity,
    SlotRequirement,
)
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.rules import default_rule_registry
from material_allocation_copilot.rules.coverage import unevaluated_slot_fields
from material_allocation_copilot.solver.models import CandidateMatrix, CandidatePair, MatrixSummary

MAX_CANDIDATE_PAIRS = 20_000
CANDIDATE_POLICY_VERSION = "candidate-policy-1.0.0"


def _eligibility_checks(material: MaterialItem, slot: SlotRequirement) -> tuple[RuleResult, ...]:
    availability = material.availability_status
    if availability is AvailabilityStatus.AVAILABLE:
        outcome = RuleOutcome.PASS
    elif availability is AvailabilityStatus.UNKNOWN:
        outcome = RuleOutcome.NOT_EVALUABLE
    else:
        outcome = RuleOutcome.FAIL
    availability_check = RuleResult(
        rule_id="candidate.inventory.availability",
        rule_version="1.0.0",
        scope=RuleScope.PAIR,
        outcome=outcome,
        severity=RuleSeverity.BLOCKING,
        reason="Only known available material may enter the verified candidate graph.",
        actual_value=availability.value,
        required_value="available",
        evidence=(
            RuleEvidence(
                field_name="availability_status",
                value=availability.value,
                source_ref=f"inventory:{material.inventory_version_id}:{material.material_id}",
            ),
        ),
    )
    unsupported = unevaluated_slot_fields(slot)
    coverage_check = RuleResult(
        rule_id="candidate.requirement.coverage",
        rule_version="1.0.0",
        scope=RuleScope.PAIR,
        outcome=RuleOutcome.NOT_EVALUABLE if unsupported else RuleOutcome.PASS,
        severity=RuleSeverity.BLOCKING,
        reason=(
            "Declared hard requirements have no evaluator in the current registry."
            if unsupported
            else "No additional unsupported hard requirement is declared."
        ),
        evidence=tuple(
            RuleEvidence(
                field_name=field,
                value=str(getattr(slot, field)),
                source_ref=slot.requirement_source_ref,
            )
            for field in unsupported
        ),
    )
    return availability_check, coverage_check


def build_candidate_matrix(
    materials: tuple[MaterialItem, ...], slots: tuple[SlotRequirement, ...]
) -> CandidateMatrix:
    if not slots:
        raise ApplicationError(ErrorCode.INPUT_INVALID, "At least one slot is required.")
    pair_count = len(materials) * len(slots)
    if pair_count > MAX_CANDIDATE_PAIRS:
        raise ApplicationError(
            ErrorCode.INPUT_INVALID,
            "Candidate matrix exceeds the bounded pilot limit; no feasibility claim was made.",
            context={"pair_count": pair_count, "maximum_pair_count": MAX_CANDIDATE_PAIRS},
        )
    materials = tuple(
        sorted(
            (MaterialItem.model_validate(item) for item in materials),
            key=lambda item: item.material_id,
        )
    )
    slots = tuple(
        sorted(
            (SlotRequirement.model_validate(slot) for slot in slots), key=lambda slot: slot.slot_id
        )
    )
    if len({item.material_id for item in materials}) != len(materials) or len(
        {slot.slot_id for slot in slots}
    ) != len(slots):
        raise ApplicationError(ErrorCode.INPUT_INVALID, "Material and slot IDs must be unique.")
    if (
        len({item.inventory_version_id for item in materials}) > 1
        or len({(slot.project_id, slot.slot_requirement_version_id) for slot in slots}) != 1
    ):
        raise ApplicationError(ErrorCode.INPUT_INVALID, "Inputs must form one versioned snapshot.")
    registry = default_rule_registry()
    if any(slot.rule_set_version != registry.rule_set_version for slot in slots):
        raise ApplicationError(
            ErrorCode.INPUT_INVALID,
            "Unsupported slot rule set version.",
            context={"supported_version": registry.rule_set_version},
        )
    pairs = []
    for slot in slots:
        for material in materials:
            rule_results = registry.evaluate_pair(material, slot)
            checks = _eligibility_checks(material, slot)
            outcomes = {
                result.outcome
                for result in (*rule_results, *checks)
                if result.severity is RuleSeverity.BLOCKING
            }
            outcome = (
                RuleOutcome.FAIL
                if RuleOutcome.FAIL in outcomes
                else RuleOutcome.NOT_EVALUABLE
                if RuleOutcome.NOT_EVALUABLE in outcomes
                else RuleOutcome.PASS
            )
            pairs.append(
                CandidatePair(
                    slot_id=slot.slot_id,
                    material_id=material.material_id,
                    outcome=outcome,
                    rule_results=rule_results,
                    eligibility_checks=checks,
                )
            )
    # Includes all source fields, policy and actual judgments; independent of input order.
    identity = {
        "candidate_policy_version": CANDIDATE_POLICY_VERSION,
        "rule_set_version": registry.rule_set_version,
        "materials": [item.model_dump(mode="json") for item in materials],
        "slots": [slot.model_dump(mode="json") for slot in slots],
        "pairs": [pair.model_dump(mode="json") for pair in pairs],
    }
    matrix_hash = hashlib.sha256(
        json.dumps(
            identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode()
    ).hexdigest()
    counts = Counter(pair.outcome for pair in pairs)
    return CandidateMatrix(
        materials,
        slots,
        tuple(pairs),
        MatrixSummary(
            matrix_hash=matrix_hash,
            rule_set_version=registry.rule_set_version,
            candidate_policy_version=CANDIDATE_POLICY_VERSION,
            slot_count=len(slots),
            material_count=len(materials),
            pair_count=pair_count,
            pass_count=counts[RuleOutcome.PASS],
            fail_count=counts[RuleOutcome.FAIL],
            not_evaluable_count=counts[RuleOutcome.NOT_EVALUABLE],
        ),
    )
