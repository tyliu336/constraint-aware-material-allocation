"""Lower/upper candidate graphs distinguish known obstruction from missing evidence."""

from material_allocation_copilot.domain import RuleOutcome
from material_allocation_copilot.solver.matching import exact_matching, greedy_matching
from material_allocation_copilot.solver.models import CandidateMatrix, FeasibilityResult


def check_feasibility(matrix: CandidateMatrix) -> FeasibilityResult:
    verified: dict[str, list[str]] = {slot.slot_id: [] for slot in matrix.slots}
    optimistic: dict[str, list[str]] = {slot.slot_id: [] for slot in matrix.slots}
    for pair in matrix.pairs:
        if pair.outcome is RuleOutcome.PASS:
            verified[pair.slot_id].append(pair.material_id)
        if pair.outcome is not RuleOutcome.FAIL:
            optimistic[pair.slot_id].append(pair.material_id)
    lower = {slot: tuple(neighbors) for slot, neighbors in verified.items()}
    upper = {slot: tuple(neighbors) for slot, neighbors in optimistic.items()}
    verified_result = exact_matching(lower)
    optimistic_result = exact_matching(upper, scope="optimistic")
    if verified_result.status == "complete":
        assessment, reason = "complete_feasible", "verified_complete_matching"
    elif optimistic_result.status == "complete":
        assessment, reason = "requires_review", "evidence_insufficient"
    else:
        assessment, reason = "infeasible", "known_constraints_prevent_complete_matching"
    return FeasibilityResult(
        assessment=assessment,
        reason=reason,
        summary=matrix.summary,
        verified_matching=verified_result,
        optimistic_matching=optimistic_result,
        greedy_baseline=greedy_matching(lower),
    )
