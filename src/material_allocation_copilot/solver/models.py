"""Immutable, evidence-bearing solver outputs; optimistic edges are never certified."""

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from material_allocation_copilot.domain import (
    MaterialItem,
    RuleOutcome,
    RuleResult,
    SlotRequirement,
)
from material_allocation_copilot.domain.models import DomainModel
from material_allocation_copilot.domain.types import Identifier, Sha256Hex
from material_allocation_copilot.rules import AllocationAssignment


class CandidatePair(DomainModel):
    slot_id: Identifier
    material_id: Identifier
    outcome: RuleOutcome
    rule_results: tuple[RuleResult, ...]
    eligibility_checks: tuple[RuleResult, ...]


class MatrixSummary(DomainModel):
    matrix_hash: Sha256Hex
    rule_set_version: Identifier
    candidate_policy_version: str
    slot_count: int
    material_count: int
    pair_count: int
    pass_count: int
    fail_count: int
    not_evaluable_count: int


@dataclass(frozen=True, slots=True)
class CandidateMatrix:
    materials: tuple[MaterialItem, ...]
    slots: tuple[SlotRequirement, ...]
    pairs: tuple[CandidatePair, ...]
    summary: MatrixSummary


class HallWitness(DomainModel):
    """All neighbors of these slots, fewer than the slots: a checkable obstruction."""

    slot_ids: tuple[Identifier, ...]
    neighbor_material_ids: tuple[Identifier, ...]
    shortage: int = Field(ge=1)


class MinimumVertexCover(DomainModel):
    """Every graph edge touches this set; its size equals maximum matching cardinality."""

    slot_ids: tuple[Identifier, ...]
    material_ids: tuple[Identifier, ...]
    size: int = Field(ge=0)

    @model_validator(mode="after")
    def size_matches_partitions(self) -> "MinimumVertexCover":
        if self.size != len(self.slot_ids) + len(self.material_ids):
            raise ValueError("vertex cover size must match its partition members")
        return self


class MatchingResult(DomainModel):
    algorithm: Literal["augmenting-path-bfs-v1", "greedy-id-order-v1"]
    graph_scope: Literal["verified", "optimistic"]
    status: Literal["complete", "no_complete_matching", "method_no_complete_solution"]
    maximum_cardinality_proven: bool
    required_slot_count: int = Field(ge=0)
    matched_count: int = Field(ge=0)
    assignments: tuple[AllocationAssignment, ...]
    unmatched_slot_ids: tuple[Identifier, ...]
    hall_witness: HallWitness | None = None
    minimum_vertex_cover: MinimumVertexCover | None = None

    @model_validator(mode="after")
    def counts_and_certificates_are_consistent(self) -> "MatchingResult":
        if self.matched_count != len(self.assignments):
            raise ValueError("matched_count must equal assignment count")
        if self.required_slot_count != self.matched_count + len(self.unmatched_slot_ids):
            raise ValueError("slot counts must include matched and unmatched slots")
        if (self.status == "complete") != (not self.unmatched_slot_ids):
            raise ValueError("complete status must match the unmatched slot set")
        if self.maximum_cardinality_proven:
            if self.minimum_vertex_cover is None:
                raise ValueError("an exact result requires a minimum vertex cover certificate")
            if self.minimum_vertex_cover.size != self.matched_count:
                raise ValueError("matching and minimum vertex cover cardinalities must agree")
        elif self.minimum_vertex_cover is not None:
            raise ValueError("a heuristic result cannot claim an exact cover certificate")
        if self.status == "no_complete_matching" and self.hall_witness is None:
            raise ValueError("exact incomplete matching requires a Hall witness")
        if self.status != "no_complete_matching" and self.hall_witness is not None:
            raise ValueError("Hall witness is only valid for exact incomplete matching")
        return self


class FeasibilityResult(DomainModel):
    model_scope: Literal["pair_constraints_and_one_material_per_slot"] = (
        "pair_constraints_and_one_material_per_slot"
    )
    optimistic_is_hypothetical: Literal[True] = True
    assessment: Literal["complete_feasible", "requires_review", "infeasible"]
    reason: Literal[
        "verified_complete_matching",
        "evidence_insufficient",
        "known_constraints_prevent_complete_matching",
    ]
    summary: MatrixSummary
    verified_matching: MatchingResult
    optimistic_matching: MatchingResult
    greedy_baseline: MatchingResult
    execution_authorized: Literal[False] = False
