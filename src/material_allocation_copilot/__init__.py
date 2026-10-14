"""Constraint-Aware Material Allocation Copilot."""

from material_allocation_copilot.config import AppConfig
from material_allocation_copilot.domain import MaterialItem, RuleResult, SlotRequirement
from material_allocation_copilot.errors import ApplicationError, ErrorCode

__all__ = [
    "AppConfig",
    "ApplicationError",
    "ErrorCode",
    "MaterialItem",
    "RuleResult",
    "SlotRequirement",
]
