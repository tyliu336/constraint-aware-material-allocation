"""Rule interfaces shared by pair rules and global constraints."""

from __future__ import annotations

from typing import Protocol

from material_allocation_copilot.domain import MaterialItem, RuleResult, SlotRequirement


class PairRule(Protocol):
    rule_id: str
    rule_version: str

    def evaluate(self, material: MaterialItem, slot: SlotRequirement) -> RuleResult: ...


class GlobalRule(Protocol):
    rule_id: str
    rule_version: str
