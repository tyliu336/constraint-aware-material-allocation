from datetime import UTC, datetime
from decimal import Decimal
from itertools import product

import pytest
from pydantic import ValidationError

import material_allocation_copilot.solver.preferences as preference_module
from material_allocation_copilot.domain import MaterialItem, RuleOutcome, SlotRequirement
from material_allocation_copilot.solver import build_candidate_matrix
from material_allocation_copilot.solver.preferences import (
    PreferenceRequest,
    PreferenceScorer,
    SoftPreference,
    normalize_preferences,
)
from material_allocation_copilot.solver.ranking import rank_weighted_matchings


def material(material_id="MAT-001", **overrides) -> MaterialItem:
    values = {
        "material_id": material_id,
        "inventory_version_id": "INV-001",
        "source_system": "fixture",
        "source_record_id": material_id,
        "source_file_sha256": "a" * 64,
        "source_row_sha256": "b" * 64,
        "length_mm": "4000",
        "section_width_mm": "100",
        "section_height_mm": "200",
        "structural_use_status": "eligible",
        "strength_grade": "C24",
        "assessment_method": "certified_visual_grading",
        "assessor_id": "GRADER-001",
        "assessment_date": "2026-10-04",
        "assessment_evidence_ref": "REPORT-001",
        "defect_data_status": "complete",
        "availability_status": "available",
    }
    return MaterialItem.model_validate({**values, **overrides})


def slot(slot_id="SLOT-001", **overrides) -> SlotRequirement:
    values = {
        "project_id": "PRJ-001",
        "slot_requirement_version_id": "SLOTS-v1",
        "slot_id": slot_id,
        "slot_role": "beam",
        "clear_span_mm": "3000",
        "end_allowance_mm": "100",
        "min_section_width_mm": "90",
        "min_section_height_mm": "190",
        "allow_section_rotation": False,
        "minimum_strength_grade": "C24",
        "orientation_requirement": "fixed",
        "rule_set_version": "rules-0.1.1",
        "requirement_source_ref": "DWG-001",
        "confirmed_by": "REVIEWER-001",
        "confirmed_at": datetime(2026, 10, 5, tzinfo=UTC),
        "source_row_sha256": "c" * 64,
    }
    return SlotRequirement.model_validate({**values, **overrides})


def preferences(*items: tuple[str, str]):
    return normalize_preferences(
        tuple(SoftPreference(preference_type=name, weight=weight) for name, weight in items)
    )


def test_weights_are_normalized_and_duplicate_types_are_rejected():
    normalized = preferences(("minimize_cut_waste", "1"), ("minimize_not_evaluable", "3"))
    assert [item.normalized_weight for item in normalized] == [Decimal("0.25"), Decimal("0.75")]
    with pytest.raises(ValidationError):
        PreferenceRequest(
            preferences=(
                SoftPreference(preference_type="minimize_cut_waste", weight="1"),
                SoftPreference(preference_type="minimize_cut_waste", weight="2"),
            )
        )
    with pytest.raises(ValidationError):
        SoftPreference(preference_type="minimize_cut_waste", weight="0")


def test_cut_waste_prefers_the_shortest_adequate_piece_without_changing_edges():
    matrix = build_candidate_matrix(
        (material("MAT-short", length_mm="3200"), material("MAT-long", length_mm="5000")),
        (slot(),),
    )
    before = tuple((item.slot_id, item.material_id, item.outcome) for item in matrix.pairs)
    scorer = PreferenceScorer(
        matrix, preferences(("minimize_cut_waste", "1")), include_not_evaluable=False
    )
    search = rank_weighted_matchings(scorer.weighted_graph(), limit=2)
    assert [plan.assignments[0].material_id for plan in search.solutions] == [
        "MAT-short",
        "MAT-long",
    ]
    assert (
        scorer.pair_scores[("SLOT-001", "MAT-short")].criteria[0].actual_value
        == "100 mm length surplus"
    )
    assert tuple((item.slot_id, item.material_id, item.outcome) for item in matrix.pairs) == before


def test_plan_hash_changes_when_missing_soft_evidence_policy_changes(monkeypatch):
    matrix = build_candidate_matrix((material(),), (slot(),))
    scorer = PreferenceScorer(
        matrix, preferences(("minimize_cut_waste", "1")), include_not_evaluable=False
    )
    assignments = rank_weighted_matchings(scorer.weighted_graph(), limit=1).solutions[0].assignments
    original_hash = scorer.score_plan(assignments, rank=1).plan_hash

    monkeypatch.setattr(
        preference_module,
        "MISSING_SOFT_EVIDENCE_POLICY",
        "explicit_not_evaluable_zero_score_v2",
    )

    assert scorer.score_plan(assignments, rank=1).plan_hash != original_hash


def test_long_stock_preference_leaves_the_rarest_long_piece_unused_when_possible():
    matrix = build_candidate_matrix(
        (
            material("MAT-short", length_mm="3200"),
            material("MAT-medium", length_mm="3600"),
            material("MAT-rare-long", length_mm="6000"),
        ),
        (slot(),),
    )
    scorer = PreferenceScorer(
        matrix,
        preferences(("preserve_scarce_long_stock", "1")),
        include_not_evaluable=False,
    )
    solutions = rank_weighted_matchings(scorer.weighted_graph(), limit=3).solutions
    assert [item.assignments[0].material_id for item in solutions] == [
        "MAT-short",
        "MAT-medium",
        "MAT-rare-long",
    ]
    long_score = scorer.pair_scores[("SLOT-001", "MAT-rare-long")].weighted_score
    assert long_score == Decimal(1) / Decimal(3)


def test_unknown_reduction_is_soft_and_missing_waste_evidence_is_explicit():
    matrix = build_candidate_matrix(
        (
            material("MAT-known"),
            material(
                "MAT-unknown",
                structural_use_status="not_evaluable",
                strength_grade=None,
                assessment_method="unknown",
                assessor_id=None,
                assessment_date=None,
                assessment_evidence_ref=None,
                defect_data_status="unknown",
                availability_status="unknown",
            ),
        ),
        (slot(clear_span_mm=None),),
    )
    scorer = PreferenceScorer(
        matrix,
        preferences(("minimize_cut_waste", "1"), ("minimize_not_evaluable", "1")),
        include_not_evaluable=True,
    )
    known = scorer.pair_scores[("SLOT-001", "MAT-known")]
    unknown = scorer.pair_scores[("SLOT-001", "MAT-unknown")]
    assert known.criteria[0].status == "not_evaluable"
    assert known.criteria[0].score is None and known.criteria[0].ranking_score == 0
    assert unknown.criteria[1].score < known.criteria[1].score
    assert matrix.pairs[0].outcome is RuleOutcome.NOT_EVALUABLE
    assert matrix.pairs[1].outcome is RuleOutcome.NOT_EVALUABLE


def test_surface_defect_preference_prioritizes_smaller_less_severe_recorded_knots():
    clean_knots = (
        {
            "face": "wide",
            "scope": "whole",
            "knot_type_code": "S0",
            "size_mm": "8",
            "source_ref": "inspection:clean:wide",
        },
        {
            "face": "narrow",
            "scope": "whole",
            "knot_type_code": "0",
            "size_mm": "0",
            "source_ref": "inspection:clean:narrow",
        },
    )
    severe_knots = (
        {
            "face": "wide",
            "scope": "whole",
            "knot_type_code": "G5",
            "size_mm": "100",
            "source_ref": "inspection:severe:wide",
        },
        {
            "face": "narrow",
            "scope": "whole",
            "knot_type_code": "S3",
            "size_mm": "70",
            "source_ref": "inspection:severe:narrow",
        },
    )
    matrix = build_candidate_matrix(
        (
            material("MAT-clean", surface_knots=clean_knots),
            material("MAT-severe", surface_knots=severe_knots),
        ),
        (slot(),),
    )
    scorer = PreferenceScorer(
        matrix,
        preferences(("minimize_surface_defects", "1")),
        include_not_evaluable=False,
    )
    solutions = rank_weighted_matchings(scorer.weighted_graph(), limit=2).solutions

    assert [item.assignments[0].material_id for item in solutions] == [
        "MAT-clean",
        "MAT-severe",
    ]
    clean = scorer.pair_scores[("SLOT-001", "MAT-clean")].criteria[0]
    severe = scorer.pair_scores[("SLOT-001", "MAT-severe")].criteria[0]
    assert clean.status == "scored"
    assert clean.score > severe.score
    assert "wide=S0/8 mm" in clean.actual_value


def test_surface_defect_preference_is_not_evaluable_without_both_whole_faces():
    matrix = build_candidate_matrix((material(surface_knots=()),), (slot(),))
    scorer = PreferenceScorer(
        matrix,
        preferences(("minimize_surface_defects", "1")),
        include_not_evaluable=False,
    )

    criterion = scorer.pair_scores[("SLOT-001", "MAT-001")].criteria[0]
    assert criterion.status == "not_evaluable"
    assert criterion.ranking_score == 0


def brute_force(graph):
    slots = tuple(sorted(graph))
    solutions = []
    for chosen in product(*(tuple(sorted(graph[slot_id])) for slot_id in slots)):
        if len(set(chosen)) != len(chosen):
            continue
        identity = tuple(zip(slots, chosen, strict=True))
        total = sum((graph[slot_id][material_id] for slot_id, material_id in identity), Decimal(0))
        solutions.append((total, identity))
    return sorted(solutions, key=lambda item: (-item[0], item[1]))


def test_exact_top_k_matches_exhaustive_ranking_for_all_512_three_by_three_graphs():
    slots = ("S0", "S1", "S2")
    materials = ("M0", "M1", "M2")
    edges = tuple(product(slots, materials))
    weights = {
        edge: Decimal(2**index) / Decimal(2 ** len(edges)) for index, edge in enumerate(edges)
    }
    for mask in range(1 << len(edges)):
        graph = {
            slot_id: {
                material_id: weights[(slot_id, material_id)]
                for material_id in materials
                if mask & (1 << edges.index((slot_id, material_id)))
            }
            for slot_id in slots
        }
        expected = brute_force(graph)[:5]
        actual = rank_weighted_matchings(graph, limit=5)
        assert [(item.total_score, item.identity) for item in actual.solutions] == expected
        assert len({item.identity for item in actual.solutions}) == len(actual.solutions)
        assert actual.status == (
            "exact_top_k"
            if len(expected) == 5
            else "all_feasible_plans_enumerated"
            if expected
            else "infeasible"
        )


def test_exact_top_k_is_stable_and_unique_when_all_plan_scores_tie():
    graph = {
        slot_id: {material_id: Decimal("0.5") for material_id in ("M0", "M1", "M2")}
        for slot_id in ("S0", "S1", "S2")
    }
    expected = brute_force(graph)[:5]
    first = rank_weighted_matchings(graph, limit=5)
    second = rank_weighted_matchings(graph, limit=5)

    assert [(item.total_score, item.identity) for item in first.solutions] == expected
    assert first.solutions == second.solutions
    assert len({item.identity for item in first.solutions}) == 5


def test_exact_top_k_matches_exhaustive_ranking_for_dense_four_by_four_graph():
    slots = ("S0", "S1", "S2", "S3")
    materials = ("M0", "M1", "M2", "M3")
    edges = tuple(product(slots, materials))
    graph = {
        slot_id: {
            material_id: Decimal(2 ** edges.index((slot_id, material_id)))
            / Decimal(2 ** len(edges))
            for material_id in materials
        }
        for slot_id in slots
    }

    expected = brute_force(graph)[:5]
    actual = rank_weighted_matchings(graph, limit=5)

    assert [(item.total_score, item.identity) for item in actual.solutions] == expected
    assert len({item.identity for item in actual.solutions}) == 5
    assert actual.status == "exact_top_k"


def test_ranking_budget_exhaustion_is_explicit_and_retains_only_exact_prefix():
    graph = {
        "S1": {"M1": Decimal("1"), "M2": Decimal("0.5")},
        "S2": {"M1": Decimal("0.5"), "M2": Decimal("1")},
    }
    result = rank_weighted_matchings(graph, limit=2, subproblem_budget=1)
    assert result.status == "budget_exhausted"
    assert len(result.solutions) == 1
    assert result.solutions[0].identity == (("S1", "M1"), ("S2", "M2"))
