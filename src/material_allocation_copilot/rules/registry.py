"""Versioned rule registry with deterministic evaluation order."""

from __future__ import annotations

from dataclasses import dataclass

from material_allocation_copilot.domain import MaterialItem, RuleResult, SlotRequirement
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.rules.base import GlobalRule, PairRule
from material_allocation_copilot.rules.global_constraints import UniqueMaterialUseRule
from material_allocation_copilot.rules.pair import (
    GradeMinimumRule,
    LengthMinimumRule,
    MidspanDefectRule,
    SectionMinimumRule,
    StructuralEvidenceRule,
)


@dataclass(frozen=True, slots=True)
class RuleRegistry:
    pair_rules: tuple[PairRule, ...]
    global_rules: tuple[GlobalRule, ...]
    rule_set_version: str = "rules-0.1.1"

    def __post_init__(self) -> None:
        identities = [
            (rule.rule_id, rule.rule_version) for rule in (*self.pair_rules, *self.global_rules)
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("rule registry contains duplicate rule id/version identities")

    def evaluate_pair(
        self, material: MaterialItem, slot: SlotRequirement
    ) -> tuple[RuleResult, ...]:
        if slot.rule_set_version != self.rule_set_version:
            raise ApplicationError(
                ErrorCode.INPUT_INVALID,
                "Slot rule set version does not match the selected registry.",
                context={"supported_version": self.rule_set_version},
            )
        return tuple(rule.evaluate(material, slot) for rule in self.pair_rules)


def default_rule_registry() -> RuleRegistry:
    return RuleRegistry(
        pair_rules=(
            LengthMinimumRule(),
            SectionMinimumRule(),
            StructuralEvidenceRule(),
            GradeMinimumRule(),
            MidspanDefectRule(),
        ),
        global_rules=(UniqueMaterialUseRule(),),
    )
