"""Deterministic material-to-slot hard rules."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from material_allocation_copilot.domain import (
    DefectDataStatus,
    DefectSeverity,
    MaterialItem,
    RuleEvidence,
    RuleOutcome,
    RuleResult,
    RuleScope,
    RuleSeverity,
    SlotRequirement,
    StructuralUseStatus,
)


def _evidence(
    field_name: str, value: object, source_ref: str, unit: str | None = None
) -> RuleEvidence:
    return RuleEvidence(field_name=field_name, value=value, unit=unit, source_ref=source_ref)


@dataclass(frozen=True, slots=True)
class LengthMinimumRule:
    rule_id: str = "hard.length.minimum"
    rule_version: str = "1.0.1"

    def evaluate(self, material: MaterialItem, slot: SlotRequirement) -> RuleResult:
        source_ref = f"{material.source_system}:{material.source_record_id}:length_mm"
        if slot.clear_span_mm is None:
            return RuleResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                scope=RuleScope.PAIR,
                outcome=RuleOutcome.NOT_EVALUABLE,
                severity=RuleSeverity.BLOCKING,
                reason="Slot clear span is unknown; required material length cannot be calculated.",
                actual_value=material.length_mm,
                evidence=(_evidence("length_mm", material.length_mm, source_ref, "mm"),),
            )

        required_length = slot.clear_span_mm + slot.end_allowance_mm
        outcome = RuleOutcome.PASS if material.length_mm >= required_length else RuleOutcome.FAIL
        return RuleResult(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            scope=RuleScope.PAIR,
            outcome=outcome,
            severity=RuleSeverity.BLOCKING,
            reason=(
                f"Material length {material.length_mm} mm is "
                f"{'at least' if outcome is RuleOutcome.PASS else 'below'} "
                f"required {required_length} mm."
            ),
            actual_value=material.length_mm,
            required_value=required_length,
            evidence=(
                _evidence("length_mm", material.length_mm, source_ref, "mm"),
                _evidence(
                    "clear_span_mm",
                    slot.clear_span_mm,
                    slot.requirement_source_ref,
                    "mm",
                ),
                _evidence(
                    "end_allowance_mm",
                    slot.end_allowance_mm,
                    slot.requirement_source_ref,
                    "mm",
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class SectionMinimumRule:
    rule_id: str = "hard.section.minimum"
    rule_version: str = "1.0.0"

    def evaluate(self, material: MaterialItem, slot: SlotRequirement) -> RuleResult:
        direct = (
            material.section_width_mm >= slot.min_section_width_mm
            and material.section_height_mm >= slot.min_section_height_mm
        )
        rotated = (
            slot.allow_section_rotation
            and material.section_width_mm >= slot.min_section_height_mm
            and material.section_height_mm >= slot.min_section_width_mm
        )
        passed = direct or rotated
        source_ref = f"{material.source_system}:{material.source_record_id}:section"
        return RuleResult(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            scope=RuleScope.PAIR,
            outcome=RuleOutcome.PASS if passed else RuleOutcome.FAIL,
            severity=RuleSeverity.BLOCKING,
            reason=(
                "Material section satisfies the minimum section requirement"
                + (" after permitted rotation." if rotated and not direct else ".")
                if passed
                else "Material section is below the minimum section requirement."
            ),
            actual_value=f"{material.section_width_mm}x{material.section_height_mm}",
            required_value=f"{slot.min_section_width_mm}x{slot.min_section_height_mm}",
            evidence=(
                _evidence("section_width_mm", material.section_width_mm, source_ref, "mm"),
                _evidence("section_height_mm", material.section_height_mm, source_ref, "mm"),
                _evidence(
                    "min_section_width_mm",
                    slot.min_section_width_mm,
                    slot.requirement_source_ref,
                    "mm",
                ),
                _evidence(
                    "min_section_height_mm",
                    slot.min_section_height_mm,
                    slot.requirement_source_ref,
                    "mm",
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class StructuralEvidenceRule:
    rule_id: str = "hard.structural_evidence.required"
    rule_version: str = "1.0.1"

    def evaluate(self, material: MaterialItem, slot: SlotRequirement) -> RuleResult:
        del slot
        status = material.structural_use_status
        source_ref = f"{material.source_system}:{material.source_record_id}:assessment"
        if status is StructuralUseStatus.INELIGIBLE:
            outcome = RuleOutcome.FAIL
            reason = "Material is explicitly ineligible for structural use."
        elif status is StructuralUseStatus.ELIGIBLE:
            outcome = RuleOutcome.PASS
            reason = "Material has a traceable structural-use assessment."
        elif status is StructuralUseStatus.RESTRICTED:
            outcome = RuleOutcome.NOT_EVALUABLE
            reason = "Restricted use requires slot-specific permission evidence, not yet supported."
        else:
            outcome = RuleOutcome.NOT_EVALUABLE
            reason = "Traceable structural-use assessment evidence is unavailable."

        return RuleResult(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            scope=RuleScope.PAIR,
            outcome=outcome,
            severity=RuleSeverity.BLOCKING,
            reason=reason,
            actual_value=status.value,
            required_value="eligible or explicitly permitted restricted use",
            evidence=(_evidence("structural_use_status", status.value, source_ref),),
        )


@dataclass(frozen=True, slots=True)
class GradeMinimumRule:
    rule_id: str = "hard.grade.minimum"
    rule_version: str = "1.0.0"
    grade_rank: tuple[str, ...] = ("C16", "C18", "C24", "C30")

    def evaluate(self, material: MaterialItem, slot: SlotRequirement) -> RuleResult:
        source_ref = f"{material.source_system}:{material.source_record_id}:grade"
        if material.structural_use_status is StructuralUseStatus.INELIGIBLE:
            return RuleResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                scope=RuleScope.PAIR,
                outcome=RuleOutcome.FAIL,
                severity=RuleSeverity.BLOCKING,
                reason="Material source status is structurally ineligible.",
                actual_value=material.strength_grade,
                required_value=slot.minimum_strength_grade,
                evidence=(
                    _evidence(
                        "structural_use_status",
                        material.structural_use_status.value,
                        source_ref,
                    ),
                ),
            )

        required_grade = slot.minimum_strength_grade
        actual_grade = material.strength_grade
        if required_grade is None:
            return RuleResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                scope=RuleScope.PAIR,
                outcome=RuleOutcome.NOT_EVALUABLE,
                severity=RuleSeverity.BLOCKING,
                reason="Slot uses a capacity reference; grade-only rule is not applicable.",
                actual_value=actual_grade,
                evidence=(_evidence("strength_grade", actual_grade, source_ref),),
            )
        if actual_grade not in self.grade_rank or required_grade not in self.grade_rank:
            return RuleResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                scope=RuleScope.PAIR,
                outcome=RuleOutcome.NOT_EVALUABLE,
                severity=RuleSeverity.BLOCKING,
                reason="Actual or required strength grade is outside the configured grade order.",
                actual_value=actual_grade,
                required_value=required_grade,
                evidence=(_evidence("strength_grade", actual_grade, source_ref),),
            )

        passed = self.grade_rank.index(actual_grade) >= self.grade_rank.index(required_grade)
        return RuleResult(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            scope=RuleScope.PAIR,
            outcome=RuleOutcome.PASS if passed else RuleOutcome.FAIL,
            severity=RuleSeverity.BLOCKING,
            reason=(
                f"Material grade {actual_grade} "
                f"{'meets' if passed else 'is below'} required grade {required_grade}."
            ),
            actual_value=actual_grade,
            required_value=required_grade,
            evidence=(
                _evidence("strength_grade", actual_grade, source_ref),
                _evidence(
                    "minimum_strength_grade",
                    required_grade,
                    slot.requirement_source_ref,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class MidspanDefectRule:
    rule_id: str = "hard.defect.midspan"
    rule_version: str = "1.0.1"
    midspan_start: Decimal = Decimal("0.33")
    midspan_end: Decimal = Decimal("0.67")

    def evaluate(self, material: MaterialItem, slot: SlotRequirement) -> RuleResult:
        role = slot.slot_role.lower()
        if role != "beam" and not role.endswith("_beam"):
            return RuleResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                scope=RuleScope.PAIR,
                outcome=RuleOutcome.PASS,
                severity=RuleSeverity.BLOCKING,
                reason="Midspan beam-defect rule does not apply to this slot role.",
            )

        for defect in material.defects:
            if (
                defect.severity is DefectSeverity.MAJOR
                and defect.position_ratio is not None
                and self.midspan_start <= defect.position_ratio <= self.midspan_end
            ):
                return RuleResult(
                    rule_id=self.rule_id,
                    rule_version=self.rule_version,
                    scope=RuleScope.PAIR,
                    outcome=RuleOutcome.FAIL,
                    severity=RuleSeverity.BLOCKING,
                    reason="A major defect is inside the configured midspan exclusion zone.",
                    actual_value=defect.position_ratio,
                    required_value=f"outside {self.midspan_start}-{self.midspan_end}",
                    evidence=(
                        _evidence(
                            "defect_position_ratio",
                            defect.position_ratio,
                            defect.source_ref,
                        ),
                    ),
                )

        incomplete = material.defect_data_status is not DefectDataStatus.COMPLETE
        unresolved = any(
            defect.severity is DefectSeverity.UNKNOWN or defect.position_ratio is None
            for defect in material.defects
        )
        if incomplete or unresolved:
            return RuleResult(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                scope=RuleScope.PAIR,
                outcome=RuleOutcome.NOT_EVALUABLE,
                severity=RuleSeverity.BLOCKING,
                reason="Defect evidence lacks complete position or severity semantics.",
                evidence=tuple(
                    _evidence(
                        "defect_observation",
                        defect.defect_type,
                        defect.source_ref,
                    )
                    for defect in material.defects
                ),
            )

        return RuleResult(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            scope=RuleScope.PAIR,
            outcome=RuleOutcome.PASS,
            severity=RuleSeverity.BLOCKING,
            reason="Complete defect evidence contains no major defect in the midspan zone.",
        )
