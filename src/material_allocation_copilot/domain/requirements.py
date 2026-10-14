"""Unconfirmed semantic requirement contracts; none of these authorize solving."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from material_allocation_copilot.domain.enums import (
    OrientationRequirement,
    VisibleSurfaceScope,
)
from material_allocation_copilot.domain.types import Identifier

DraftText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class DraftModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class RequirementDraftStatus(StrEnum):
    NEEDS_CLARIFICATION = "needs_clarification"
    READY_FOR_CONFIRMATION = "ready_for_confirmation"
    PARSE_FAILED = "parse_failed"
    CONFIRMED = "confirmed"


class DraftIssueCode(StrEnum):
    AMBIGUOUS_SCOPE = "ambiguous_scope"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    CONFLICTING_PREFERENCE = "conflicting_preference"
    SUSPECTED_HARD_RULE_CHANGE = "suspected_hard_rule_change"
    MODEL_REPORTED_CONFLICT = "model_reported_conflict"


class DraftIssue(DraftModel):
    issue_id: Identifier
    code: DraftIssueCode
    severity: Literal["blocking", "warning"]
    field_path: DraftText | None
    message: DraftText
    evidence_quote: DraftText | None


class ClarificationQuestion(DraftModel):
    question_id: Identifier
    field_path: DraftText
    question: DraftText
    answer_kind: Literal["text", "integer", "decimal", "boolean", "choice"]
    options: tuple[DraftText, ...]
    required: bool


class DraftSlotProposal(DraftModel):
    slot_id: Identifier | None
    slot_role: DraftText | None
    clear_span_mm: int | None = Field(ge=1)
    end_allowance_mm: int | None = Field(ge=0)
    min_section_width_mm: int | None = Field(ge=1)
    min_section_height_mm: int | None = Field(ge=1)
    allow_section_rotation: bool | None
    minimum_strength_grade: DraftText | None
    capacity_requirement_ref: DraftText | None
    orientation_requirement: OrientationRequirement | None
    defect_restriction_profile_id: Identifier | None
    max_moisture_content_pct: float | None = Field(ge=0, le=100)
    treatment_restriction_profile_id: Identifier | None
    visible_surface_scope: VisibleSurfaceScope | None
    aesthetic_requirement_text: DraftText | None
    visual_reference_ref: DraftText | None
    rule_set_version: Identifier | None
    requirement_source_ref: DraftText | None

    @model_validator(mode="after")
    def structural_basis_is_not_invented(self) -> DraftSlotProposal:
        if self.minimum_strength_grade is not None and self.capacity_requirement_ref is not None:
            raise ValueError(
                "draft slot must not merge grade and capacity bases without clarification"
            )
        return self


class DraftSoftPreference(DraftModel):
    preference_type: Literal[
        "minimize_cut_waste",
        "preserve_scarce_long_stock",
        "minimize_not_evaluable",
        "minimize_surface_defects",
    ]
    requested_weight: float | None = Field(gt=0, le=1000)
    scope: DraftText | None
    rationale: DraftText


class InterpretedSoftPreference(DraftModel):
    preference_type: Literal[
        "minimize_cut_waste",
        "preserve_scarce_long_stock",
        "minimize_not_evaluable",
        "minimize_surface_defects",
    ]
    weight: float = Field(gt=0, le=10)
    rationale: DraftText


class ModelPreferenceInterpretation(DraftModel):
    summary: DraftText
    preferences: tuple[InterpretedSoftPreference, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def preference_types_are_unique(self) -> ModelPreferenceInterpretation:
        preference_types = [item.preference_type for item in self.preferences]
        if len(preference_types) != len(set(preference_types)):
            raise ValueError("preference types must be unique")
        return self


class PreferenceInterpretationView(DraftModel):
    summary: DraftText
    preferences: tuple[InterpretedSoftPreference, ...] = Field(min_length=1, max_length=3)
    model_id: DraftText
    prompt_version: str
    prompt_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ModelRequirementInterpretation(DraftModel):
    summary: DraftText
    slots: tuple[DraftSlotProposal, ...] = Field(max_length=100)
    soft_preferences: tuple[DraftSoftPreference, ...] = Field(max_length=3)
    issues: tuple[DraftIssue, ...] = Field(max_length=100)
    clarification_questions: tuple[ClarificationQuestion, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> ModelRequirementInterpretation:
        issue_ids = [item.issue_id for item in self.issues]
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("draft issue_id values must be unique")
        question_ids = [item.question_id for item in self.clarification_questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("clarification question_id values must be unique")
        return self


class RequirementDraftView(DraftModel):
    draft_id: Identifier
    project_id: Identifier
    status: RequirementDraftStatus
    revision: int = Field(ge=1)
    source_text: DraftText
    source_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    interpretation: ModelRequirementInterpretation | None
    parse_error: DraftText | None
    prompt_name: Identifier
    prompt_version: str
    prompt_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: DraftText
    attempt_count: int = Field(ge=1, le=2)
    context_snapshot_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=2)
    context_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed_requirement_id: Identifier | None
