"""Authorized write entry points used by the HTTP API."""

from datetime import UTC, datetime

from material_allocation_copilot.application.access import Principal, Role
from material_allocation_copilot.application.agent_runtime import (
    AgentModel,
    BoundedAgentOrchestrator,
    UnavailableAgentModel,
)
from material_allocation_copilot.application.candidates import CandidateService
from material_allocation_copilot.application.contracts import (
    AllocationPlanInput,
    ApprovalDecision,
    ApprovalInput,
    ConfirmedRequirementInput,
    InventoryImportInput,
    PreferenceInterpretationInput,
    ProjectCreateInput,
    RequirementDraftClarifyInput,
    RequirementDraftConfirmInput,
    RequirementDraftCreateInput,
    ReservationCreateInput,
    ReservationReleaseInput,
    TaskCancelInput,
    TaskCreateInput,
    TaskRetryInput,
)
from material_allocation_copilot.application.preference_interpretation import (
    PreferenceInterpretationService,
)
from material_allocation_copilot.application.queries import QueryService
from material_allocation_copilot.application.ranking import RankingService
from material_allocation_copilot.application.semantic_requirements import (
    RequirementDraftService,
    StructuredRequirementModel,
)
from material_allocation_copilot.application.services import (
    AllocationService,
    ApprovalService,
    InventoryService,
    ProjectService,
    RecoveryService,
    RequirementService,
    ReservationService,
    TaskService,
    UnitOfWorkFactory,
)
from material_allocation_copilot.application.tooling import ToolExecutor
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.prompts import default_prompt_registry


class WorkflowService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        requirement_model: StructuredRequirementModel,
        agent_model: AgentModel | None = None,
    ) -> None:
        self.projects = ProjectService(uow_factory)
        self.inventories = InventoryService(uow_factory)
        self.requirements = RequirementService(uow_factory)
        self.tasks = TaskService(uow_factory)
        self.allocations = AllocationService(uow_factory)
        self.approvals = ApprovalService(uow_factory)
        self.reservations = ReservationService(uow_factory)
        self.recovery = RecoveryService(uow_factory)
        self.queries = QueryService(uow_factory)
        self.candidates = CandidateService(uow_factory)
        self.rankings = RankingService(uow_factory)
        self.tools = ToolExecutor(uow_factory)
        self.agent_runs = BoundedAgentOrchestrator(
            uow_factory,
            agent_model or UnavailableAgentModel(),
            tool_executor=self.tools,
        )
        self.requirement_drafts = RequirementDraftService(
            uow_factory, requirement_model, default_prompt_registry()
        )
        self.preference_interpretations = PreferenceInterpretationService(
            uow_factory, requirement_model, default_prompt_registry()
        )

    def create_project(self, principal: Principal, request: ProjectCreateInput):
        principal.require_project(request.project_id, Role.PLANNER)
        return self.projects.create_project(**request.model_dump())

    def import_inventory(self, principal: Principal, request: InventoryImportInput):
        principal.require_project(request.project_id, Role.PLANNER)
        return self.inventories.import_inventory(request)

    def confirm_requirement(self, principal: Principal, request: ConfirmedRequirementInput):
        principal.require_project(request.project_id, Role.REVIEWER)
        if any(slot.confirmed_by != principal.user_id for slot in request.slots):
            raise ApplicationError(
                ErrorCode.FORBIDDEN, "Confirmation identity must match the caller."
            )
        return self.requirements.save_confirmed(request)

    def create_requirement_draft(self, principal: Principal, request: RequirementDraftCreateInput):
        principal.require_project(request.project_id, Role.PLANNER)
        return self.requirement_drafts.create(request, created_by=principal.user_id)

    def interpret_preferences(self, principal: Principal, request: PreferenceInterpretationInput):
        principal.require_project(request.project_id, Role.PLANNER)
        return self.preference_interpretations.interpret(request)

    def get_requirement_draft(self, principal: Principal, project_id: str, draft_id: str):
        principal.require_project(project_id)
        return self.requirement_drafts.get(project_id, draft_id)

    def clarify_requirement_draft(
        self, principal: Principal, request: RequirementDraftClarifyInput
    ):
        principal.require_project(request.project_id, Role.PLANNER)
        return self.requirement_drafts.clarify(request)

    def confirm_requirement_draft(
        self, principal: Principal, request: RequirementDraftConfirmInput
    ):
        principal.require_project(request.project_id, Role.REVIEWER)
        return self.requirement_drafts.confirm(request, confirmed_by=principal.user_id)

    def create_task(self, principal: Principal, request: TaskCreateInput):
        principal.require_project(request.project_id, Role.PLANNER)
        # Parent resources are checked in this scope before the lower-level transaction.
        self.queries.get_inventory(principal, request.project_id, request.inventory_version_id)
        self.queries.get_requirement(principal, request.project_id, request.requirement_id)
        return self.tasks.create_task(**request.model_dump())

    def save_plan(self, principal: Principal, project_id: str, request: AllocationPlanInput):
        principal.require_project(project_id, Role.PLANNER)
        self.queries.get_task(principal, project_id, request.allocation_task_id)
        return self.allocations.save_plan(request)

    def cancel_task(self, principal: Principal, project_id: str, request: TaskCancelInput):
        principal.require_project(project_id, Role.PLANNER)
        self.queries.get_task(principal, project_id, request.allocation_task_id)
        return self.tasks.cancel(request)

    def retry_task(self, principal: Principal, project_id: str, request: TaskRetryInput):
        principal.require_project(project_id, Role.PLANNER)
        self.queries.get_task(principal, project_id, request.retry_of_task_id)
        return self.tasks.retry(request)

    def decide(
        self,
        principal: Principal,
        project_id: str,
        plan_id: str,
        *,
        approval_id: str,
        idempotency_key: str,
        decision: ApprovalDecision,
        note: str | None,
    ):
        principal.require_project(project_id, Role.REVIEWER)
        _, existing = self.queries.get_plan(principal, project_id, plan_id)
        # The server owns decision time. Retrying must retain the original server time.
        decided_at = existing.decided_at if existing is not None else datetime.now(UTC)
        return self.approvals.record_decision(
            ApprovalInput(
                approval_id=approval_id,
                allocation_plan_id=plan_id,
                idempotency_key=idempotency_key,
                decision=decision,
                decided_by=principal.user_id,
                decided_at=decided_at,
                note=note,
            )
        )

    def reserve_plan(
        self,
        principal: Principal,
        project_id: str,
        plan_id: str,
        *,
        reservation_id: str,
        idempotency_key: str,
    ):
        principal.require_project(project_id, Role.REVIEWER)
        self.queries.get_plan(principal, project_id, plan_id)
        return self.reservations.reserve(
            ReservationCreateInput(
                reservation_id=reservation_id,
                allocation_plan_id=plan_id,
                idempotency_key=idempotency_key,
                reserved_by=principal.user_id,
            )
        )

    def get_reservation(self, principal: Principal, project_id: str, reservation_id: str):
        principal.require_project(project_id)
        record = self.reservations.get(reservation_id)
        if record.project_id != project_id:
            raise ApplicationError(ErrorCode.NOT_FOUND, "Inventory reservation was not found.")
        return record

    def release_reservation(
        self,
        principal: Principal,
        project_id: str,
        reservation_id: str,
        *,
        idempotency_key: str,
    ):
        principal.require_project(project_id, Role.REVIEWER)
        self.get_reservation(principal, project_id, reservation_id)
        return self.reservations.release(
            ReservationReleaseInput(
                reservation_id=reservation_id,
                idempotency_key=idempotency_key,
                released_by=principal.user_id,
            )
        )
