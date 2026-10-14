"""Immutable product-domain contracts independent of persistence and AI frameworks."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from material_allocation_copilot.domain.enums import (
    AssessmentMethod,
    AvailabilityStatus,
    DefectDataStatus,
    DefectSeverity,
    OrientationRequirement,
    ReuseHistoryStatus,
    RuleOutcome,
    RuleScope,
    RuleSeverity,
    StructuralUseStatus,
    TreatmentContaminationStatus,
    VisibleSurfaceScope,
)
from material_allocation_copilot.domain.types import (
    Identifier,
    NonNegativeMillimetres,
    Percentage,
    PositiveMillimetres,
    RuleIdentifier,
    SemanticVersion,
    Sha256Hex,
)

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
NotesText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
EvidenceValue = str | int | float | bool | Decimal | None


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        use_enum_values=False,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


def _require_timezone(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return value


class DefectObservation(DomainModel):
    defect_type: ShortText
    severity: DefectSeverity = DefectSeverity.UNKNOWN
    position_ratio: Annotated[Decimal, Field(ge=0, le=1)] | None = None
    source_ref: ShortText
    note: NotesText | None = None


class SurfaceKnotObservation(DomainModel):
    face: Literal["wide", "narrow"]
    scope: Literal["whole", "tested"]
    knot_type_code: Annotated[str, StringConstraints(pattern=r"^(0|[SG][0-5])$")]
    size_mm: NonNegativeMillimetres | None = None
    source_ref: ShortText


class MaterialItem(DomainModel):
    material_id: Identifier
    inventory_version_id: Identifier
    source_system: Identifier
    source_record_id: Identifier
    source_file_sha256: Sha256Hex
    source_row_sha256: Sha256Hex
    measured_at: datetime | None = None
    length_mm: PositiveMillimetres
    section_width_mm: PositiveMillimetres
    section_height_mm: PositiveMillimetres

    scan_batch_id: Identifier | None = None
    source_asset_ref: ShortText | None = None
    species: ShortText | None = None
    moisture_content_pct: Percentage | None = None

    structural_use_status: StructuralUseStatus = StructuralUseStatus.NOT_EVALUABLE
    strength_grade: ShortText | None = None
    design_property_ref: ShortText | None = None
    assessment_method: AssessmentMethod = AssessmentMethod.UNKNOWN
    assessor_id: Identifier | None = None
    assessment_date: date | None = None
    assessment_evidence_ref: ShortText | None = None

    defect_data_status: DefectDataStatus = DefectDataStatus.UNKNOWN
    defect_evidence_ref: ShortText | None = None
    defects: tuple[DefectObservation, ...] = Field(default_factory=tuple)
    surface_knots: tuple[SurfaceKnotObservation, ...] = Field(default_factory=tuple)
    reuse_history_status: ReuseHistoryStatus = ReuseHistoryStatus.UNKNOWN
    treatment_contamination_status: TreatmentContaminationStatus = (
        TreatmentContaminationStatus.UNKNOWN
    )
    availability_status: AvailabilityStatus = AvailabilityStatus.UNKNOWN

    storage_location: ShortText | None = None
    image_manifest_ref: ShortText | None = None
    notes: NotesText | None = None

    @field_validator("measured_at")
    @classmethod
    def measured_at_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_timezone(value, "measured_at")

    @model_validator(mode="after")
    def structural_claim_requires_assessment_evidence(self) -> MaterialItem:
        if self.structural_use_status not in {
            StructuralUseStatus.ELIGIBLE,
            StructuralUseStatus.RESTRICTED,
        }:
            return self

        missing_fields: list[str] = []
        if self.assessment_method is AssessmentMethod.UNKNOWN:
            missing_fields.append("assessment_method")
        if self.assessor_id is None:
            missing_fields.append("assessor_id")
        if self.assessment_date is None:
            missing_fields.append("assessment_date")
        if self.assessment_evidence_ref is None:
            missing_fields.append("assessment_evidence_ref")
        if self.strength_grade is None and self.design_property_ref is None:
            missing_fields.append("strength_grade_or_design_property_ref")

        if missing_fields:
            joined = ", ".join(missing_fields)
            raise ValueError(f"structural eligibility claim is missing evidence: {joined}")
        return self


class SlotRequirement(DomainModel):
    project_id: Identifier
    slot_requirement_version_id: Identifier
    slot_id: Identifier
    slot_role: ShortText

    clear_span_mm: PositiveMillimetres | None = None
    end_allowance_mm: NonNegativeMillimetres
    min_section_width_mm: PositiveMillimetres
    min_section_height_mm: PositiveMillimetres
    allow_section_rotation: bool

    minimum_strength_grade: ShortText | None = None
    capacity_requirement_ref: ShortText | None = None
    orientation_requirement: OrientationRequirement
    defect_restriction_profile_id: Identifier | None = None
    max_moisture_content_pct: Percentage | None = None
    treatment_restriction_profile_id: Identifier | None = None

    visible_surface_scope: VisibleSurfaceScope = VisibleSurfaceScope.NONE
    aesthetic_requirement_text: NotesText | None = None
    visual_reference_ref: ShortText | None = None

    rule_set_version: Identifier
    requirement_source_ref: ShortText
    confirmed_by: Identifier
    confirmed_at: datetime
    source_row_sha256: Sha256Hex

    @field_validator("confirmed_at")
    @classmethod
    def confirmed_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone(value, "confirmed_at")

    @model_validator(mode="after")
    def structural_basis_is_required(self) -> SlotRequirement:
        if self.minimum_strength_grade is None and self.capacity_requirement_ref is None:
            raise ValueError("minimum_strength_grade or capacity_requirement_ref is required")
        return self


class RuleEvidence(DomainModel):
    field_name: Identifier
    value: EvidenceValue
    unit: ShortText | None = None
    source_ref: ShortText


class RuleResult(DomainModel):
    rule_id: RuleIdentifier
    rule_version: SemanticVersion
    scope: RuleScope
    outcome: RuleOutcome
    severity: RuleSeverity
    reason: NonEmptyText
    actual_value: EvidenceValue = None
    required_value: EvidenceValue = None
    evidence: tuple[RuleEvidence, ...] = Field(default_factory=tuple)
