"""Authorized, read-only candidate generation and solver preview over task snapshots."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from material_allocation_copilot.application.access import Principal, Role
from material_allocation_copilot.application.contracts import (
    AllocationPlanInput,
    PlanTerminalStatus,
    RuleEvaluationInput,
)
from material_allocation_copilot.application.plan_validation import verify_plan
from material_allocation_copilot.application.queries import QueryService, TaskInputs
from material_allocation_copilot.application.services import UnitOfWorkFactory
from material_allocation_copilot.domain import RuleOutcome
from material_allocation_copilot.domain.types import Identifier, Sha256Hex
from material_allocation_copilot.rules import AllocationAssignment
from material_allocation_copilot.solver import build_candidate_matrix, check_feasibility
from material_allocation_copilot.solver.models import (
    CandidatePair,
    FeasibilityResult,
    MatrixSummary,
)


class CandidatePage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    allocation_task_id: Identifier
    input_snapshot_hash: Sha256Hex
    summary: MatrixSummary
    items: tuple[CandidatePair, ...]
    total: int
    limit: int
    offset: int


class SolvePreview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    allocation_task_id: Identifier
    input_snapshot_hash: Sha256Hex
    analysis: FeasibilityResult
    terminal_status: PlanTerminalStatus
    assignment_basis: Literal["verified", "optimistic"]
    assignments: tuple[AllocationAssignment, ...]
    rule_evaluations: tuple[RuleEvaluationInput, ...]
    candidate_eligibility_checks: tuple[RuleEvaluationInput, ...]
    preview_only: Literal[True] = True
    execution_authorized: Literal[False] = False


class FeasibilityPreview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    allocation_task_id: Identifier
    input_snapshot_hash: Sha256Hex
    analysis: FeasibilityResult
    terminal_status: PlanTerminalStatus
    preview_only: Literal[True] = True
    execution_authorized: Literal[False] = False


class CandidateService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self.queries = QueryService(uow_factory)

    def list_pairs(
        self,
        principal: Principal,
        project_id: str,
        task_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        outcome: RuleOutcome | None = None,
    ) -> CandidatePage:
        QueryService._pagination(limit, offset)
        inputs = self.queries.load_task_inputs(principal, project_id, task_id)
        matrix = build_candidate_matrix(inputs.materials, inputs.slots)
        pairs = tuple(pair for pair in matrix.pairs if outcome is None or pair.outcome is outcome)
        return CandidatePage(
            allocation_task_id=task_id,
            input_snapshot_hash=inputs.input_snapshot_hash,
            summary=matrix.summary,
            items=pairs[offset : offset + limit],
            total=len(pairs),
            limit=limit,
            offset=offset,
        )

    def solve(self, principal: Principal, project_id: str, task_id: str) -> SolvePreview:
        principal.require_project(project_id, Role.PLANNER)
        inputs: TaskInputs = self.queries.load_task_inputs(principal, project_id, task_id)
        matrix = build_candidate_matrix(inputs.materials, inputs.slots)
        analysis = check_feasibility(matrix)
        status = PlanTerminalStatus(analysis.assessment)
        optimistic = status is PlanTerminalStatus.REQUIRES_REVIEW
        matching = analysis.optimistic_matching if optimistic else analysis.verified_matching
        assignments = matching.assignments
        # Recheck the entire proposed plan through the existing authoritative save validator.
        # This is a preview only: no plan IDs, task state, approval or stock are written.
        evaluations = verify_plan(
            AllocationPlanInput(
                allocation_plan_id="PREVIEW",
                allocation_task_id=task_id,
                plan_version=1,
                terminal_status=status,
                input_snapshot_hash=inputs.input_snapshot_hash,
                assignments=assignments,
                rule_evaluations=(),
            ),
            inputs.materials,
            inputs.slots,
        )
        selected = {(item.slot_id, item.material_id) for item in assignments}
        checks = tuple(
            RuleEvaluationInput(material_id=pair.material_id, slot_id=pair.slot_id, result=check)
            for pair in matrix.pairs
            if (pair.slot_id, pair.material_id) in selected
            for check in pair.eligibility_checks
        )
        return SolvePreview(
            allocation_task_id=task_id,
            input_snapshot_hash=inputs.input_snapshot_hash,
            analysis=analysis,
            terminal_status=status,
            assignment_basis="optimistic" if optimistic else "verified",
            assignments=assignments,
            rule_evaluations=evaluations,
            candidate_eligibility_checks=checks,
        )

    def check_feasibility(
        self, principal: Principal, project_id: str, task_id: str
    ) -> FeasibilityPreview:
        principal.require_project(project_id, Role.PLANNER)
        inputs = self.queries.load_task_inputs(principal, project_id, task_id)
        matrix = build_candidate_matrix(inputs.materials, inputs.slots)
        analysis = check_feasibility(matrix)
        return FeasibilityPreview(
            allocation_task_id=task_id,
            input_snapshot_hash=inputs.input_snapshot_hash,
            analysis=analysis,
            terminal_status=PlanTerminalStatus(analysis.assessment),
        )
