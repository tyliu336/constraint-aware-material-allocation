from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from material_allocation_copilot.domain import (
    AssessmentMethod,
    DefectDataStatus,
    DefectObservation,
    DefectSeverity,
    MaterialItem,
    OrientationRequirement,
    RuleOutcome,
    SlotRequirement,
    StructuralUseStatus,
)
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.rules import (
    AllocationAssignment,
    GradeMinimumRule,
    LengthMinimumRule,
    MidspanDefectRule,
    RuleRegistry,
    SectionMinimumRule,
    StructuralEvidenceRule,
    UniqueMaterialUseRule,
    default_rule_registry,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def make_material(**overrides: object) -> MaterialItem:
    values: dict[str, object] = {
        "material_id": "MAT-001",
        "inventory_version_id": "INV-001",
        "source_system": "test_fixture",
        "source_record_id": "ROW-001",
        "source_file_sha256": HASH_A,
        "source_row_sha256": HASH_B,
        "measured_at": datetime(2026, 10, 4, 9, 0, tzinfo=UTC),
        "length_mm": "4000",
        "section_width_mm": "100",
        "section_height_mm": "200",
        "structural_use_status": StructuralUseStatus.ELIGIBLE,
        "strength_grade": "C24",
        "assessment_method": AssessmentMethod.CERTIFIED_VISUAL_GRADING,
        "assessor_id": "GRADER-01",
        "assessment_date": date(2026, 10, 3),
        "assessment_evidence_ref": "REPORT-001",
        "defect_data_status": DefectDataStatus.COMPLETE,
    }
    values.update(overrides)
    return MaterialItem.model_validate(values)


def make_slot(**overrides: object) -> SlotRequirement:
    values: dict[str, object] = {
        "project_id": "PRJ-001",
        "slot_requirement_version_id": "SLOTS-v1",
        "slot_id": "SLOT-B01",
        "slot_role": "beam",
        "clear_span_mm": "3000",
        "end_allowance_mm": "100",
        "min_section_width_mm": "90",
        "min_section_height_mm": "190",
        "allow_section_rotation": False,
        "minimum_strength_grade": "C24",
        "orientation_requirement": OrientationRequirement.FIXED,
        "rule_set_version": "rules-0.1.1",
        "requirement_source_ref": "DWG-S201#B01",
        "confirmed_by": "ENG-01",
        "confirmed_at": datetime(2026, 10, 4, 9, 30, tzinfo=UTC),
        "source_row_sha256": HASH_A,
    }
    values.update(overrides)
    return SlotRequirement.model_validate(values)


def test_length_exact_boundary_passes() -> None:
    result = LengthMinimumRule().evaluate(make_material(length_mm="3100"), make_slot())

    assert result.outcome is RuleOutcome.PASS
    assert result.required_value == Decimal("3100")


def test_length_short_material_fails() -> None:
    result = LengthMinimumRule().evaluate(make_material(length_mm="3099.9"), make_slot())

    assert result.outcome is RuleOutcome.FAIL


def test_length_is_not_evaluable_without_clear_span() -> None:
    result = LengthMinimumRule().evaluate(make_material(), make_slot(clear_span_mm=None))

    assert result.outcome is RuleOutcome.NOT_EVALUABLE


def test_section_can_pass_after_explicitly_permitted_rotation() -> None:
    material = make_material(section_width_mm="200", section_height_mm="100")
    slot = make_slot(allow_section_rotation=True)

    assert SectionMinimumRule().evaluate(material, slot).outcome is RuleOutcome.PASS


def test_section_fails_when_rotation_is_forbidden() -> None:
    material = make_material(section_width_mm="200", section_height_mm="100")

    assert SectionMinimumRule().evaluate(material, make_slot()).outcome is RuleOutcome.FAIL


def test_grade_below_requirement_fails() -> None:
    material = make_material(strength_grade="C18")

    assert GradeMinimumRule().evaluate(material, make_slot()).outcome is RuleOutcome.FAIL


def test_source_ineligible_material_fails_grade_rule() -> None:
    material = make_material(
        structural_use_status=StructuralUseStatus.INELIGIBLE,
        strength_grade=None,
        assessment_method=AssessmentMethod.UNKNOWN,
        assessor_id=None,
        assessment_date=None,
        assessment_evidence_ref=None,
    )

    assert GradeMinimumRule().evaluate(material, make_slot()).outcome is RuleOutcome.FAIL


def test_unknown_structural_evidence_is_not_evaluable() -> None:
    material = make_material(
        structural_use_status=StructuralUseStatus.NOT_EVALUABLE,
        assessment_method=AssessmentMethod.UNKNOWN,
        assessor_id=None,
        assessment_date=None,
        assessment_evidence_ref=None,
    )

    result = StructuralEvidenceRule().evaluate(material, make_slot())

    assert result.outcome is RuleOutcome.NOT_EVALUABLE


def test_restricted_material_requires_slot_specific_permission() -> None:
    material = make_material(structural_use_status=StructuralUseStatus.RESTRICTED)
    assert (
        StructuralEvidenceRule().evaluate(material, make_slot()).outcome
        is RuleOutcome.NOT_EVALUABLE
    )


def test_treetrace_style_partial_defect_evidence_is_not_evaluable() -> None:
    material = make_material(
        defect_data_status=DefectDataStatus.PARTIAL,
        defects=(
            DefectObservation(
                defect_type="observed_weak_point",
                severity=DefectSeverity.UNKNOWN,
                position_ratio=Decimal("0.5"),
                source_ref="treetrace:1:weakPointx_m",
            ),
        ),
    )

    result = MidspanDefectRule().evaluate(material, make_slot())

    assert result.outcome is RuleOutcome.NOT_EVALUABLE


def test_known_major_midspan_defect_fails() -> None:
    material = make_material(
        defects=(
            DefectObservation(
                defect_type="knot",
                severity=DefectSeverity.MAJOR,
                position_ratio=Decimal("0.5"),
                source_ref="inspection:001",
            ),
        ),
    )

    assert MidspanDefectRule().evaluate(material, make_slot()).outcome is RuleOutcome.FAIL


@pytest.mark.parametrize("role", ["secondary_beam", "primary_beam"])
def test_beam_roles_do_not_skip_the_midspan_rule(role: str) -> None:
    material = make_material(
        defects=(
            DefectObservation(
                defect_type="knot",
                severity=DefectSeverity.MAJOR,
                position_ratio=Decimal("0.5"),
                source_ref="inspection:001",
            ),
        )
    )
    assert (
        MidspanDefectRule().evaluate(material, make_slot(slot_role=role)).outcome
        is RuleOutcome.FAIL
    )


def test_midspan_defect_rule_is_not_applicable_to_column() -> None:
    material = make_material(defect_data_status=DefectDataStatus.UNKNOWN)
    slot = make_slot(slot_role="column")

    assert MidspanDefectRule().evaluate(material, slot).outcome is RuleOutcome.PASS


def test_unique_material_use_rejects_duplicate_and_locked_material() -> None:
    assignments = (
        AllocationAssignment(slot_id="S-1", material_id="M-1"),
        AllocationAssignment(slot_id="S-2", material_id="M-1"),
        AllocationAssignment(slot_id="S-3", material_id="M-2"),
    )

    result = UniqueMaterialUseRule().evaluate(
        assignments,
        unavailable_material_ids=frozenset({"M-2"}),
    )

    assert result.outcome is RuleOutcome.FAIL
    assert result.actual_value == "M-1,M-2"


def test_default_registry_has_stable_order_and_three_state_results() -> None:
    registry = default_rule_registry()
    results = registry.evaluate_pair(make_material(), make_slot())

    assert [result.rule_id for result in results] == [
        "hard.length.minimum",
        "hard.section.minimum",
        "hard.structural_evidence.required",
        "hard.grade.minimum",
        "hard.defect.midspan",
    ]
    assert all(result.outcome is RuleOutcome.PASS for result in results)


def test_registry_rejects_duplicate_rule_identity() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        RuleRegistry(
            pair_rules=(LengthMinimumRule(), LengthMinimumRule()),
            global_rules=(),
        )


def test_registry_rejects_an_unsupported_requirement_rule_version() -> None:
    with pytest.raises(ApplicationError) as captured:
        default_rule_registry().evaluate_pair(
            make_material(), make_slot(rule_set_version="unknown-v1")
        )
    assert captured.value.code is ErrorCode.INPUT_INVALID
