"""Versioned soft preferences score candidate edges without changing hard eligibility."""

import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from material_allocation_copilot.domain import RuleOutcome, RuleSeverity
from material_allocation_copilot.domain.models import DomainModel
from material_allocation_copilot.domain.types import Identifier, Sha256Hex
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.rules import AllocationAssignment
from material_allocation_copilot.solver.models import CandidateMatrix, CandidatePair

SCORING_POLICY_VERSION = "soft-scoring-1.1.0"
MISSING_SOFT_EVIDENCE_POLICY = "explicit_not_evaluable_zero_score_v1"


def _content_hash(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SoftPreferenceType(StrEnum):
    MINIMIZE_CUT_WASTE = "minimize_cut_waste"
    PRESERVE_SCARCE_LONG_STOCK = "preserve_scarce_long_stock"
    MINIMIZE_NOT_EVALUABLE = "minimize_not_evaluable"
    MINIMIZE_SURFACE_DEFECTS = "minimize_surface_defects"


class SoftPreference(DomainModel):
    preference_type: SoftPreferenceType
    weight: Decimal = Field(gt=0, le=1000, max_digits=10, decimal_places=6)


class PreferenceRequest(DomainModel):
    preferences: tuple[SoftPreference, ...] = Field(min_length=1, max_length=3)
    max_plans: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def preference_types_are_unique(self) -> "PreferenceRequest":
        types = [item.preference_type for item in self.preferences]
        if len(types) != len(set(types)):
            raise ValueError("soft preference types must be unique")
        return self


class NormalizedPreference(DomainModel):
    preference_type: SoftPreferenceType
    requested_weight: Decimal
    normalized_weight: Decimal = Field(ge=0, le=1)


class CriterionScore(DomainModel):
    preference_type: SoftPreferenceType
    status: Literal["scored", "not_evaluable"]
    score: Decimal | None = Field(default=None, ge=0, le=1)
    ranking_score: Decimal = Field(ge=0, le=1)
    reason: str = Field(min_length=1)
    actual_value: str | None = None
    ideal_value: str | None = None


class PairSoftScore(DomainModel):
    slot_id: Identifier
    material_id: Identifier
    weighted_score: Decimal = Field(ge=0, le=1)
    criteria: tuple[CriterionScore, ...]


class PreferencePlanScore(DomainModel):
    preference_type: SoftPreferenceType
    normalized_weight: Decimal = Field(ge=0, le=1)
    score: Decimal = Field(ge=0, le=1)
    weighted_score: Decimal = Field(ge=0, le=1)
    evaluable_assignment_count: int = Field(ge=0)
    assignment_count: int = Field(ge=0)


class RankedPlan(DomainModel):
    rank: int = Field(ge=1)
    plan_hash: Sha256Hex
    total_score: Decimal = Field(ge=0, le=1)
    assignments: tuple[AllocationAssignment, ...]
    preference_scores: tuple[PreferencePlanScore, ...]
    assignment_scores: tuple[PairSoftScore, ...]
    deterministic_recheck_completed: Literal[True] = True
    execution_authorized: Literal[False] = False


def normalize_preferences(
    preferences: tuple[SoftPreference, ...],
) -> tuple[NormalizedPreference, ...]:
    if not preferences:
        raise ApplicationError(ErrorCode.INPUT_INVALID, "At least one soft preference is required.")
    if len({item.preference_type for item in preferences}) != len(preferences):
        raise ApplicationError(ErrorCode.INPUT_INVALID, "Soft preference types must be unique.")
    total = sum((item.weight for item in preferences), Decimal(0))
    return tuple(
        NormalizedPreference(
            preference_type=item.preference_type,
            requested_weight=item.weight,
            normalized_weight=item.weight / total,
        )
        for item in sorted(preferences, key=lambda item: item.preference_type.value)
    )


class PreferenceScorer:
    def __init__(
        self,
        matrix: CandidateMatrix,
        preferences: tuple[NormalizedPreference, ...],
        *,
        include_not_evaluable: bool,
    ) -> None:
        self.matrix = matrix
        self.preferences = preferences
        self.materials = {item.material_id: item for item in matrix.materials}
        self.slots = {item.slot_id: item for item in matrix.slots}
        self.pairs = {(item.slot_id, item.material_id): item for item in matrix.pairs}
        allowed = {RuleOutcome.PASS}
        if include_not_evaluable:
            allowed.add(RuleOutcome.NOT_EVALUABLE)
        self.active_pairs = tuple(pair for pair in matrix.pairs if pair.outcome in allowed)
        active_material_ids = sorted({pair.material_id for pair in self.active_pairs})
        self.long_stock_scores = {
            material_id: (
                Decimal(
                    sum(
                        self.materials[other_id].length_mm >= self.materials[material_id].length_mm
                        for other_id in active_material_ids
                    )
                )
                / Decimal(len(active_material_ids))
            )
            for material_id in active_material_ids
        }
        self.pair_scores = {
            (pair.slot_id, pair.material_id): self._score_pair(pair) for pair in self.active_pairs
        }

    def _required_length(self, slot_id: str) -> Decimal | None:
        slot = self.slots[slot_id]
        if slot.clear_span_mm is None:
            return None
        return slot.clear_span_mm + slot.end_allowance_mm

    @staticmethod
    def _missing(preference_type: SoftPreferenceType, reason: str) -> CriterionScore:
        return CriterionScore(
            preference_type=preference_type,
            status="not_evaluable",
            score=None,
            ranking_score=Decimal(0),
            reason=reason,
        )

    def _criterion(
        self, pair: CandidatePair, preference_type: SoftPreferenceType
    ) -> CriterionScore:
        material = self.materials[pair.material_id]
        required = self._required_length(pair.slot_id)
        if preference_type is SoftPreferenceType.MINIMIZE_CUT_WASTE:
            if required is None:
                return self._missing(preference_type, "Required cut length is unavailable.")
            utilization = min(Decimal(1), required / material.length_mm)
            return CriterionScore(
                preference_type=preference_type,
                status="scored",
                score=utilization,
                ranking_score=utilization,
                reason="Higher required-length utilization leaves less geometric length surplus.",
                actual_value=f"{material.length_mm - required} mm length surplus",
                ideal_value="0 mm length surplus",
            )
        if preference_type is SoftPreferenceType.PRESERVE_SCARCE_LONG_STOCK:
            score = self.long_stock_scores[pair.material_id]
            return CriterionScore(
                preference_type=preference_type,
                status="scored",
                score=score,
                ranking_score=score,
                reason=(
                    "Using more replaceable shorter stock receives a higher score, preserving "
                    "rare long pieces when a complete matching does not need them."
                ),
                actual_value=f"{material.length_mm} mm material length",
                ideal_value="use the most replaceable adequate stock",
            )
        if preference_type is SoftPreferenceType.MINIMIZE_SURFACE_DEFECTS:
            observations = {
                item.face: item for item in material.surface_knots if item.scope == "whole"
            }
            if set(observations) != {"wide", "narrow"}:
                return self._missing(
                    preference_type,
                    "Whole-board knot evidence is unavailable for both wide and narrow faces.",
                )
            penalties: list[Decimal] = []
            details: list[str] = []
            face_dimensions = {
                "wide": material.section_height_mm,
                "narrow": material.section_width_mm,
            }
            for face in ("wide", "narrow"):
                observation = observations[face]
                code = observation.knot_type_code
                if code == "0":
                    penalties.append(Decimal(0))
                    details.append(f"{face}=none")
                    continue
                if observation.size_mm is None:
                    return self._missing(
                        preference_type,
                        f"Whole-board {face}-face knot size is unavailable.",
                    )
                relative_size = min(Decimal(1), observation.size_mm / face_dimensions[face])
                condition_factor = Decimal("0.5") + Decimal(int(code[-1])) / Decimal(10)
                grouping_factor = Decimal("1.15") if code.startswith("G") else Decimal(1)
                penalty = min(Decimal(1), relative_size * condition_factor * grouping_factor)
                penalties.append(penalty)
                details.append(f"{face}={code}/{observation.size_mm} mm")
            score = Decimal(1) - sum(penalties, Decimal(0)) / Decimal(len(penalties))
            return CriterionScore(
                preference_type=preference_type,
                status="scored",
                score=score,
                ranking_score=score,
                reason=(
                    "Smaller recorded whole-board knots and less severe knot condition codes "
                    "receive a higher surface-quality score."
                ),
                actual_value="; ".join(details),
                ideal_value="no recorded whole-board knot on either face",
            )
        blocking = tuple(
            result
            for result in (*pair.rule_results, *pair.eligibility_checks)
            if result.severity is RuleSeverity.BLOCKING
        )
        unknown = sum(result.outcome is RuleOutcome.NOT_EVALUABLE for result in blocking)
        score = Decimal(len(blocking) - unknown) / Decimal(len(blocking))
        return CriterionScore(
            preference_type=preference_type,
            status="scored",
            score=score,
            ranking_score=score,
            reason="Fewer blocking not-evaluable checks receive a higher soft score.",
            actual_value=f"{unknown} of {len(blocking)} blocking checks not evaluable",
            ideal_value="0 blocking checks not evaluable",
        )

    def _score_pair(self, pair: CandidatePair) -> PairSoftScore:
        criteria = tuple(self._criterion(pair, item.preference_type) for item in self.preferences)
        weights = {item.preference_type: item.normalized_weight for item in self.preferences}
        weighted = sum(
            (weights[item.preference_type] * item.ranking_score for item in criteria), Decimal(0)
        )
        return PairSoftScore(
            slot_id=pair.slot_id,
            material_id=pair.material_id,
            weighted_score=weighted,
            criteria=criteria,
        )

    def weighted_graph(self) -> dict[str, dict[str, Decimal]]:
        graph: dict[str, dict[str, Decimal]] = {slot.slot_id: {} for slot in self.matrix.slots}
        for (slot_id, material_id), score in self.pair_scores.items():
            graph[slot_id][material_id] = score.weighted_score
        return graph

    def score_plan(self, assignments: tuple[AllocationAssignment, ...], *, rank: int) -> RankedPlan:
        assignment_scores = tuple(
            self.pair_scores[(item.slot_id, item.material_id)]
            for item in sorted(assignments, key=lambda item: item.slot_id)
        )
        breakdown = []
        for preference in self.preferences:
            criteria = tuple(
                next(
                    item
                    for item in pair_score.criteria
                    if item.preference_type is preference.preference_type
                )
                for pair_score in assignment_scores
            )
            score = (
                sum((item.ranking_score for item in criteria), Decimal(0)) / Decimal(len(criteria))
                if criteria
                else Decimal(0)
            )
            breakdown.append(
                PreferencePlanScore(
                    preference_type=preference.preference_type,
                    normalized_weight=preference.normalized_weight,
                    score=score,
                    weighted_score=score * preference.normalized_weight,
                    evaluable_assignment_count=sum(item.status == "scored" for item in criteria),
                    assignment_count=len(criteria),
                )
            )
        total = sum((item.weighted_score for item in breakdown), Decimal(0))
        canonical_assignments = [
            item.model_dump(mode="json")
            for item in sorted(assignments, key=lambda item: item.slot_id)
        ]
        plan_hash = _content_hash(
            {
                "schema": "ranked-plan-v1",
                "matrix_hash": self.matrix.summary.matrix_hash,
                "scoring_policy_version": SCORING_POLICY_VERSION,
                "missing_soft_evidence_policy": MISSING_SOFT_EVIDENCE_POLICY,
                "preferences": [item.model_dump(mode="json") for item in self.preferences],
                "assignments": canonical_assignments,
            }
        )
        return RankedPlan(
            rank=rank,
            plan_hash=plan_hash,
            total_score=total,
            assignments=tuple(sorted(assignments, key=lambda item: item.slot_id)),
            preference_scores=tuple(breakdown),
            assignment_scores=assignment_scores,
        )
