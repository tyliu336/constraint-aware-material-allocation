from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from material_allocation_copilot.domain import (
    AssessmentMethod,
    AvailabilityStatus,
    MaterialItem,
    OrientationRequirement,
    RuleEvidence,
    RuleOutcome,
    RuleResult,
    RuleScope,
    RuleSeverity,
    SlotRequirement,
    StructuralUseStatus,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def make_material(**overrides: object) -> MaterialItem:
    values: dict[str, object] = {
        "material_id": "MAT-001",
        "inventory_version_id": "INV-001",
        "source_system": "treetrace",
        "source_record_id": "TT-001",
        "source_file_sha256": HASH_A,
        "source_row_sha256": HASH_B,
        "measured_at": datetime(2026, 10, 4, 9, 0, tzinfo=UTC),
        "length_mm": "4000.125",
        "section_width_mm": "100",
        "section_height_mm": "200",
    }
    values.update(overrides)
    return MaterialItem.model_validate(values)


def make_slot(**overrides: object) -> SlotRequirement:
    values: dict[str, object] = {
        "project_id": "PRJ-001",
        "slot_requirement_version_id": "SLOTS-v1",
        "slot_id": "SLOT-B01",
        "slot_role": "secondary_beam",
        "clear_span_mm": "3000",
        "end_allowance_mm": "100",
        "min_section_width_mm": "90",
        "min_section_height_mm": "190",
        "allow_section_rotation": False,
        "minimum_strength_grade": "C24",
        "orientation_requirement": OrientationRequirement.FIXED,
        "rule_set_version": "rules-0.1.0",
        "requirement_source_ref": "DWG-S201#B01",
        "confirmed_by": "ENG-01",
        "confirmed_at": datetime(2026, 10, 4, 9, 30, tzinfo=UTC),
        "source_row_sha256": HASH_A,
    }
    values.update(overrides)
    return SlotRequirement.model_validate(values)


def test_material_preserves_decimal_measurement_and_defaults_to_not_evaluable() -> None:
    material = make_material()

    assert str(material.length_mm) == "4000.125"
    assert material.structural_use_status is StructuralUseStatus.NOT_EVALUABLE
    assert material.availability_status is AvailabilityStatus.UNKNOWN


def test_material_allows_unknown_measurement_time_without_fabricating_one() -> None:
    material = make_material(measured_at=None)

    assert material.measured_at is None


def test_material_rejects_non_positive_dimension() -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        make_material(length_mm=0)


def test_material_rejects_timezone_naive_measurement() -> None:
    with pytest.raises(ValidationError, match="timezone offset"):
        make_material(measured_at=datetime(2026, 10, 4, 9, 0))


def test_structural_eligibility_requires_traceable_assessment() -> None:
    with pytest.raises(ValidationError, match="structural eligibility claim"):
        make_material(structural_use_status=StructuralUseStatus.ELIGIBLE)


def test_structural_eligibility_accepts_complete_assessment() -> None:
    material = make_material(
        structural_use_status=StructuralUseStatus.ELIGIBLE,
        strength_grade="C24",
        assessment_method=AssessmentMethod.CERTIFIED_VISUAL_GRADING,
        assessor_id="GRADER-01",
        assessment_date=date(2026, 10, 3),
        assessment_evidence_ref="REPORT-001",
    )

    assert material.structural_use_status is StructuralUseStatus.ELIGIBLE


def test_domain_models_forbid_unknown_fields_and_mutation() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        make_material(paper_condition="A")

    material = make_material()
    with pytest.raises(ValidationError, match="frozen"):
        material.length_mm = 1  # type: ignore[misc]


def test_slot_requires_grade_or_capacity_basis() -> None:
    with pytest.raises(ValidationError, match="capacity_requirement_ref"):
        make_slot(minimum_strength_grade=None, capacity_requirement_ref=None)


def test_slot_accepts_capacity_reference_instead_of_grade() -> None:
    slot = make_slot(
        minimum_strength_grade=None,
        capacity_requirement_ref="CALC-001#B01",
    )

    assert slot.capacity_requirement_ref == "CALC-001#B01"


@pytest.mark.parametrize(
    "outcome",
    [RuleOutcome.PASS, RuleOutcome.FAIL, RuleOutcome.NOT_EVALUABLE],
)
def test_rule_result_preserves_three_state_outcome(outcome: RuleOutcome) -> None:
    result = RuleResult(
        rule_id="hard.length.minimum",
        rule_version="1.0.0",
        scope=RuleScope.PAIR,
        outcome=outcome,
        severity=RuleSeverity.BLOCKING,
        reason="Evidence-backed result.",
        actual_value="4000.125",
        required_value="3100",
        evidence=(
            RuleEvidence(
                field_name="length_mm",
                value="4000.125",
                unit="mm",
                source_ref="TT-001:length",
            ),
        ),
    )

    assert result.outcome is outcome
    assert result.evidence[0].source_ref == "TT-001:length"
