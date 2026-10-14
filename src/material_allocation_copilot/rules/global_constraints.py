"""Deterministic plan-level constraints."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from material_allocation_copilot.domain import (
    RuleEvidence,
    RuleOutcome,
    RuleResult,
    RuleScope,
    RuleSeverity,
)
from material_allocation_copilot.domain.types import Identifier


class AllocationAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    slot_id: Identifier
    material_id: Identifier


@dataclass(frozen=True, slots=True)
class UniqueMaterialUseRule:
    rule_id: str = "global.inventory.unique_use"
    rule_version: str = "1.0.0"

    def evaluate(
        self,
        assignments: tuple[AllocationAssignment, ...],
        *,
        unavailable_material_ids: frozenset[str] = frozenset(),
    ) -> RuleResult:
        counts = Counter(item.material_id for item in assignments)
        duplicate_ids = sorted(material_id for material_id, count in counts.items() if count > 1)
        unavailable_ids = sorted(set(counts) & unavailable_material_ids)
        conflicts = sorted(set(duplicate_ids) | set(unavailable_ids))

        if conflicts:
            return RuleResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                scope=RuleScope.GLOBAL,
                outcome=RuleOutcome.FAIL,
                severity=RuleSeverity.BLOCKING,
                reason="A material is assigned more than once or is unavailable.",
                actual_value=",".join(conflicts),
                required_value="each material used once and available",
                evidence=tuple(
                    RuleEvidence(
                        field_name="material_id",
                        value=material_id,
                        source_ref="allocation_plan",
                    )
                    for material_id in conflicts
                ),
            )

        return RuleResult(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            scope=RuleScope.GLOBAL,
            outcome=RuleOutcome.PASS,
            severity=RuleSeverity.BLOCKING,
            reason="Every assigned material is unique and not listed as unavailable.",
        )
