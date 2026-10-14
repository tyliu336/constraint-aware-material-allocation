from datetime import UTC, datetime
from itertools import product

import pytest

from material_allocation_copilot.domain import MaterialItem, RuleOutcome, SlotRequirement
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.solver import build_candidate_matrix, check_feasibility
from material_allocation_copilot.solver.candidates import MAX_CANDIDATE_PAIRS
from material_allocation_copilot.solver.matching import exact_matching, greedy_matching


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


def test_all_pair_outcomes_and_reasons_are_preserved():
    matrix = build_candidate_matrix(
        (
            material("MAT-good"),
            material("MAT-short", length_mm="1000"),
            material("MAT-unknown", structural_use_status="not_evaluable"),
        ),
        (slot(),),
    )
    assert matrix.summary.pair_count == 3
    assert (
        matrix.summary.pass_count,
        matrix.summary.fail_count,
        matrix.summary.not_evaluable_count,
    ) == (1, 1, 1)
    assert [pair.outcome for pair in matrix.pairs] == [
        RuleOutcome.PASS,
        RuleOutcome.FAIL,
        RuleOutcome.NOT_EVALUABLE,
    ]
    for pair in matrix.pairs:
        assert len(pair.rule_results) == 5 and len(pair.eligibility_checks) == 2
        assert all(result.reason and result.rule_version for result in pair.rule_results)
    failure = matrix.pairs[1].rule_results[0]
    assert failure.rule_id == "hard.length.minimum"
    assert failure.outcome is RuleOutcome.FAIL and failure.evidence


def test_fail_dominates_unknown_and_optimism_does_not_rescue_known_failure():
    matrix = build_candidate_matrix(
        (material(length_mm="1000", availability_status="unknown"),), (slot(),)
    )
    assert matrix.pairs[0].outcome is RuleOutcome.FAIL
    result = check_feasibility(matrix)
    assert result.assessment == "infeasible"
    assert result.optimistic_matching.hall_witness.neighbor_material_ids == ()


@pytest.mark.parametrize(
    "status,outcome",
    [
        ("available", RuleOutcome.PASS),
        ("unknown", RuleOutcome.NOT_EVALUABLE),
        ("reserved", RuleOutcome.FAIL),
        ("allocated", RuleOutcome.FAIL),
        ("unavailable", RuleOutcome.FAIL),
    ],
)
def test_snapshot_availability_is_a_candidate_guard(status, outcome):
    pair = build_candidate_matrix((material(availability_status=status),), (slot(),)).pairs[0]
    assert pair.outcome is outcome
    assert pair.eligibility_checks[0].outcome is outcome


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_moisture_content_pct", "20"),
        ("treatment_restriction_profile_id", "TREATMENT-001"),
        ("defect_restriction_profile_id", "DEFECT-001"),
        ("capacity_requirement_ref", "CAPACITY-001"),
    ],
)
def test_unimplemented_declared_constraints_cannot_be_verified(field, value):
    matrix = build_candidate_matrix((material(),), (slot(**{field: value}),))
    assert matrix.pairs[0].outcome is RuleOutcome.NOT_EVALUABLE
    assert matrix.pairs[0].eligibility_checks[1].evidence[0].field_name == field
    result = check_feasibility(matrix)
    assert result.assessment == "requires_review"
    assert result.verified_matching.assignments == ()
    assert result.optimistic_matching.status == "complete"


def test_unknown_evidence_is_not_a_proof_of_infeasibility():
    matrix = build_candidate_matrix((material(structural_use_status="not_evaluable"),), (slot(),))
    result = check_feasibility(matrix)
    assert result.assessment == "requires_review"
    assert result.reason == "evidence_insufficient"
    assert result.verified_matching.status == "no_complete_matching"
    assert result.optimistic_matching.status == "complete"
    assert result.execution_authorized is False


def test_shared_material_conflict_has_a_checkable_hall_witness_even_with_unknowns():
    matrix = build_candidate_matrix(
        (material(availability_status="unknown"),), (slot("SLOT-001"), slot("SLOT-002"))
    )
    result = check_feasibility(matrix)
    assert result.assessment == "infeasible"
    witness = result.optimistic_matching.hall_witness
    assert witness.slot_ids == ("SLOT-001", "SLOT-002")
    assert witness.neighbor_material_ids == ("MAT-001",) and witness.shortage == 1


def test_exact_matching_recovers_from_greedy_resource_competition():
    # Short material cannot satisfy SLOT-002; ID-ordered greedy wastes the unique long material.
    matrix = build_candidate_matrix(
        (material("MAT-001", length_mm="5000"), material("MAT-002", length_mm="3200")),
        (slot("SLOT-001"), slot("SLOT-002", clear_span_mm="4500")),
    )
    result = check_feasibility(matrix)
    assert result.assessment == "complete_feasible"
    assert [(item.slot_id, item.material_id) for item in result.verified_matching.assignments] == [
        ("SLOT-001", "MAT-002"),
        ("SLOT-002", "MAT-001"),
    ]
    assert result.greedy_baseline.status == "method_no_complete_solution"
    assert result.greedy_baseline.maximum_cardinality_proven is False
    assert result.greedy_baseline.hall_witness is None


def test_matrix_hash_and_solver_results_are_stable_across_input_order():
    materials, slots = (
        (material("MAT-001"), material("MAT-002")),
        (slot("SLOT-001"), slot("SLOT-002")),
    )
    matrix = build_candidate_matrix(materials, slots)
    reverse = build_candidate_matrix(tuple(reversed(materials)), tuple(reversed(slots)))
    assert matrix == reverse
    assert check_feasibility(matrix) == check_feasibility(reverse)
    changed = build_candidate_matrix((material("MAT-001", length_mm="4001"), materials[1]), slots)
    assert changed.summary.matrix_hash != matrix.summary.matrix_hash


def test_empty_inventory_is_a_proven_shortage_but_empty_requirements_are_invalid():
    result = check_feasibility(build_candidate_matrix((), (slot(),)))
    assert result.assessment == "infeasible"
    assert result.optimistic_matching.hall_witness.shortage == 1
    with pytest.raises(ApplicationError):
        build_candidate_matrix((material(),), ())


@pytest.mark.parametrize(
    "materials,slots",
    [
        ((material(), material()), (slot(),)),
        ((material(),), (slot(), slot())),
        ((material(), material("MAT-002", inventory_version_id="INV-other")), (slot(),)),
        ((material(),), (slot(), slot("SLOT-002", project_id="PRJ-other"))),
        ((material(),), (slot(rule_set_version="rules-0.1.0"),)),
    ],
)
def test_duplicate_mixed_snapshot_or_unsupported_version_is_rejected(materials, slots):
    with pytest.raises(ApplicationError) as caught:
        build_candidate_matrix(materials, slots)
    assert caught.value.code == ErrorCode.INPUT_INVALID


def test_pair_budget_exhaustion_is_not_reported_as_infeasible():
    with pytest.raises(ApplicationError) as caught:
        build_candidate_matrix((material(),) * (MAX_CANDIDATE_PAIRS + 1), (slot(),))
    assert caught.value.context["maximum_pair_count"] == MAX_CANDIDATE_PAIRS
    assert "no feasibility claim" in caught.value.message


def brute_force_cardinality(graph: dict[str, tuple[str, ...]]) -> int:
    choices = [(None, *neighbors) for neighbors in graph.values()]
    best = 0
    for assignment in product(*choices):
        used = [material for material in assignment if material is not None]
        if len(used) == len(set(used)):
            best = max(best, len(used))
    return best


def assert_witness(graph, result):
    cover = result.minimum_vertex_cover
    assert cover is not None
    assert cover.size == len(result.assignments)
    assert all(
        slot_id in cover.slot_ids or material_id in cover.material_ids
        for slot_id, neighbors in graph.items()
        for material_id in neighbors
    )
    witness = result.hall_witness
    if result.status == "complete":
        assert witness is None
        return
    assert witness is not None
    neighbors = set().union(*(set(graph[slot_id]) for slot_id in witness.slot_ids))
    assert neighbors == set(witness.neighbor_material_ids)
    assert witness.shortage == len(witness.slot_ids) - len(neighbors) > 0


def test_exact_matching_against_exhaustive_answers_for_all_5054_small_graphs():
    count = 0
    for slot_count in range(1, 5):
        for material_count in range(4):
            for mask in range(1 << (slot_count * material_count)):
                graph = {
                    f"SLOT-{s}": tuple(
                        f"MAT-{m}"
                        for m in range(material_count)
                        if mask & (1 << (s * material_count + m))
                    )
                    for s in range(slot_count)
                }
                result = exact_matching(graph)
                assert len(result.assignments) == brute_force_cardinality(graph)
                assert result.maximum_cardinality_proven
                assert result.matched_count == len(result.assignments)
                assert result.required_slot_count == slot_count
                assert len({item.material_id for item in result.assignments}) == len(
                    result.assignments
                )
                assert all(item.material_id in graph[item.slot_id] for item in result.assignments)
                assert_witness(graph, result)
                count += 1
    assert count == 5054


def test_iterative_matching_handles_a_path_longer_than_python_recursion_limit():
    graph = {f"SLOT-{i:04}": (f"MAT-{i:04}", f"MAT-{i + 1:04}") for i in range(1100)}
    graph["SLOT-1100"] = ("MAT-0000",)
    result = exact_matching(graph)
    assert result.status == "complete" and len(result.assignments) == 1101
    assert greedy_matching(graph).status == "method_no_complete_solution"


def test_bipartite_partitions_can_have_overlapping_ids_and_disconnected_components():
    graph = {"SHARED-ID": ("SHARED-ID",), "SLOT-001": (), "SLOT-002": ("MAT-002",)}
    result = exact_matching(graph)
    assert len(result.assignments) == 2
    assert_witness(graph, result)
