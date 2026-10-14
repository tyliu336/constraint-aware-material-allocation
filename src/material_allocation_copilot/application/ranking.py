"""Authorized soft-ranking preview over immutable task inputs and hard candidate edges."""

from decimal import Decimal
from typing import Literal

from pydantic import Field

from material_allocation_copilot.application.access import Principal, Role
from material_allocation_copilot.application.contracts import (
    AllocationPlanInput,
    PlanTerminalStatus,
)
from material_allocation_copilot.application.plan_validation import verify_plan
from material_allocation_copilot.application.queries import QueryService
from material_allocation_copilot.application.services import UnitOfWorkFactory
from material_allocation_copilot.domain.models import DomainModel
from material_allocation_copilot.domain.types import Identifier, Sha256Hex
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.rules import AllocationAssignment
from material_allocation_copilot.solver import (
    build_candidate_matrix,
    check_feasibility,
    rank_weighted_matchings,
)
from material_allocation_copilot.solver.matching import greedy_matching
from material_allocation_copilot.solver.models import FeasibilityResult
from material_allocation_copilot.solver.preferences import (
    MISSING_SOFT_EVIDENCE_POLICY,
    SCORING_POLICY_VERSION,
    NormalizedPreference,
    PreferenceRequest,
    PreferenceScorer,
    RankedPlan,
    SoftPreferenceType,
    normalize_preferences,
)

MAX_RANKING_SLOTS = 30
MAX_RANKING_EDGES = 2_500
RANKING_SUBPROBLEM_BUDGET = 250


class PreferenceDiagnostic(DomainModel):
    code: Literal["objective_tradeoff", "no_ranking_effect", "single_feasible_plan"]
    preference_types: tuple[SoftPreferenceType, ...]
    message: str = Field(min_length=1)


class BaselineScore(DomainModel):
    method: Literal["unweighted_exact", "greedy_id_order"]
    complete: bool
    total_score: Decimal | None = Field(default=None, ge=0, le=1)
    difference_from_ranked_best: Decimal | None = Field(default=None, ge=0, le=1)
    assignments: tuple[AllocationAssignment, ...]


class RankedPlansResult(DomainModel):
    allocation_task_id: Identifier
    input_snapshot_hash: Sha256Hex
    matrix_hash: Sha256Hex
    terminal_status: PlanTerminalStatus
    graph_scope: Literal["verified", "optimistic", "none"]
    feasibility: FeasibilityResult
    scoring_policy_version: Literal["soft-scoring-1.1.0"] = SCORING_POLICY_VERSION
    missing_soft_evidence_policy: Literal["explicit_not_evaluable_zero_score_v1"] = (
        MISSING_SOFT_EVIDENCE_POLICY
    )
    normalized_preferences: tuple[NormalizedPreference, ...]
    plans: tuple[RankedPlan, ...]
    search_status: Literal[
        "exact_top_k", "all_feasible_plans_enumerated", "budget_exhausted", "infeasible"
    ]
    subproblems_solved: int = Field(ge=0)
    baselines: tuple[BaselineScore, ...]
    diagnostics: tuple[PreferenceDiagnostic, ...]
    preview_only: Literal[True] = True
    execution_authorized: Literal[False] = False


def _diagnostics(plans: tuple[RankedPlan, ...]) -> tuple[PreferenceDiagnostic, ...]:
    if len(plans) == 1:
        return (
            PreferenceDiagnostic(
                code="single_feasible_plan",
                preference_types=tuple(item.preference_type for item in plans[0].preference_scores),
                message="Only one complete assignment was returned; preferences cannot reorder it.",
            ),
        )
    diagnostics = []
    preference_types = tuple(item.preference_type for item in plans[0].preference_scores)
    best_by_preference: dict[SoftPreferenceType, frozenset[str]] = {}
    for preference_type in preference_types:
        values = {
            plan.plan_hash: next(
                item.score
                for item in plan.preference_scores
                if item.preference_type is preference_type
            )
            for plan in plans
        }
        maximum = max(values.values())
        best_by_preference[preference_type] = frozenset(
            plan_hash for plan_hash, value in values.items() if value == maximum
        )
        if len(set(values.values())) == 1:
            diagnostics.append(
                PreferenceDiagnostic(
                    code="no_ranking_effect",
                    preference_types=(preference_type,),
                    message="This preference gives every returned plan the same score.",
                )
            )
    common_best = set.intersection(*(set(value) for value in best_by_preference.values()))
    if len(best_by_preference) > 1 and not common_best:
        diagnostics.append(
            PreferenceDiagnostic(
                code="objective_tradeoff",
                preference_types=preference_types,
                message="Different preference objectives select different best returned plans.",
            )
        )
    return tuple(diagnostics)


def _baseline(
    method: Literal["unweighted_exact", "greedy_id_order"],
    assignments: tuple[AllocationAssignment, ...],
    *,
    complete: bool,
    scorer: PreferenceScorer,
    best_score: Decimal,
) -> BaselineScore:
    if not complete:
        return BaselineScore(method=method, complete=False, assignments=assignments)
    score = scorer.score_plan(assignments, rank=1).total_score
    return BaselineScore(
        method=method,
        complete=True,
        total_score=score,
        difference_from_ranked_best=best_score - score,
        assignments=assignments,
    )


class RankingService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self.queries = QueryService(uow_factory)

    def rank(
        self,
        principal: Principal,
        project_id: str,
        task_id: str,
        request: PreferenceRequest,
    ) -> RankedPlansResult:
        principal.require_project(project_id, Role.PLANNER)
        request = PreferenceRequest.model_validate(request)
        inputs = self.queries.load_task_inputs(principal, project_id, task_id)
        matrix = build_candidate_matrix(inputs.materials, inputs.slots)
        feasibility = check_feasibility(matrix)
        preferences = normalize_preferences(request.preferences)
        status = PlanTerminalStatus(feasibility.assessment)
        graph_scope: Literal["verified", "optimistic", "none"] = (
            "none"
            if status is PlanTerminalStatus.INFEASIBLE
            else "optimistic"
            if status is PlanTerminalStatus.REQUIRES_REVIEW
            else "verified"
        )
        if status is PlanTerminalStatus.INFEASIBLE:
            return RankedPlansResult(
                allocation_task_id=task_id,
                input_snapshot_hash=inputs.input_snapshot_hash,
                matrix_hash=matrix.summary.matrix_hash,
                terminal_status=status,
                graph_scope=graph_scope,
                feasibility=feasibility,
                normalized_preferences=preferences,
                plans=(),
                search_status="infeasible",
                subproblems_solved=0,
                baselines=(),
                diagnostics=(),
            )
        scorer = PreferenceScorer(
            matrix,
            preferences,
            include_not_evaluable=graph_scope == "optimistic",
        )
        graph = scorer.weighted_graph()
        edge_count = sum(len(neighbors) for neighbors in graph.values())
        if len(graph) > MAX_RANKING_SLOTS or edge_count > MAX_RANKING_EDGES:
            raise ApplicationError(
                ErrorCode.INPUT_INVALID,
                "Soft ranking exceeds the bounded pilot limit; no ranking claim was made.",
                context={
                    "slot_count": len(graph),
                    "edge_count": edge_count,
                    "maximum_slot_count": MAX_RANKING_SLOTS,
                    "maximum_edge_count": MAX_RANKING_EDGES,
                },
            )
        search = rank_weighted_matchings(
            graph,
            limit=request.max_plans,
            subproblem_budget=RANKING_SUBPROBLEM_BUDGET,
        )
        if not search.solutions:
            raise ApplicationError(
                ErrorCode.INTERNAL_ERROR,
                "A complete hard-feasible graph produced no ranked matching.",
            )
        plans = []
        for rank, solution in enumerate(search.solutions, start=1):
            # Soft ordering never bypasses the existing hard-plan validator.
            verify_plan(
                AllocationPlanInput(
                    allocation_plan_id=f"RANK-PREVIEW-{rank}",
                    allocation_task_id=task_id,
                    plan_version=rank,
                    terminal_status=status,
                    input_snapshot_hash=inputs.input_snapshot_hash,
                    assignments=solution.assignments,
                    rule_evaluations=(),
                ),
                inputs.materials,
                inputs.slots,
            )
            plans.append(scorer.score_plan(solution.assignments, rank=rank))
        ranked_plans = tuple(plans)
        best_score = ranked_plans[0].total_score
        exact = (
            feasibility.verified_matching
            if graph_scope == "verified"
            else feasibility.optimistic_matching
        )
        greedy = greedy_matching(
            {slot_id: tuple(neighbors) for slot_id, neighbors in scorer.weighted_graph().items()}
        )
        baselines = (
            _baseline(
                "unweighted_exact",
                exact.assignments,
                complete=exact.status == "complete",
                scorer=scorer,
                best_score=best_score,
            ),
            _baseline(
                "greedy_id_order",
                greedy.assignments,
                complete=greedy.status == "complete",
                scorer=scorer,
                best_score=best_score,
            ),
        )
        return RankedPlansResult(
            allocation_task_id=task_id,
            input_snapshot_hash=inputs.input_snapshot_hash,
            matrix_hash=matrix.summary.matrix_hash,
            terminal_status=status,
            graph_scope=graph_scope,
            feasibility=feasibility,
            normalized_preferences=preferences,
            plans=ranked_plans,
            search_status=search.status,
            subproblems_solved=search.subproblems_solved,
            baselines=baselines,
            diagnostics=_diagnostics(ranked_plans),
        )
