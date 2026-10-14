"""Declared hard requirements not implemented by the current rule registry."""

from material_allocation_copilot.domain import SlotRequirement


def unevaluated_slot_fields(slot: SlotRequirement) -> tuple[str, ...]:
    return tuple(
        field
        for field in (
            "max_moisture_content_pct",
            "treatment_restriction_profile_id",
            "defect_restriction_profile_id",
            "capacity_requirement_ref",
        )
        if getattr(slot, field) is not None
    )
