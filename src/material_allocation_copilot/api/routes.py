"""Thin HTTP handlers: parsing and response mapping around application services."""

import hashlib
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response
from fastapi.concurrency import run_in_threadpool

from material_allocation_copilot.adapters import import_treetrace_boards
from material_allocation_copilot.adapters.csv_input import inventory_from_csv, slots_from_csv
from material_allocation_copilot.api.auth import get_principal
from material_allocation_copilot.api.schemas import (
    AgentApprovalResolutionRequest,
    AgentRunRequest,
    AgentRunResponse,
    ApprovalRequest,
    ApprovalResponse,
    CallerIdentityResponse,
    ErrorResponse,
    InventoryRequest,
    InventoryResponse,
    PageResponse,
    PairRequest,
    PairResponse,
    PlanRequest,
    PlanResponse,
    PreferenceInterpretationRequest,
    ProjectResponse,
    PromptResponse,
    RequirementClarificationRequest,
    RequirementDraftConfirmationRequest,
    RequirementDraftRequest,
    RequirementRequest,
    RequirementResponse,
    ReservationRequest,
    ReservationResponse,
    RuleEvaluationResponse,
    TaskCancelRequest,
    TaskRequest,
    TaskResponse,
    TaskRetryRequest,
    TreeTraceRequest,
)
from material_allocation_copilot.application.access import Principal, Role
from material_allocation_copilot.application.agent_runtime import (
    AgentRunInput,
    AgentRunView,
    ApprovalResolutionInput,
)
from material_allocation_copilot.application.candidates import CandidatePage, SolvePreview
from material_allocation_copilot.application.contracts import (
    AllocationPlanInput,
    ConfirmedRequirementInput,
    InventoryImportInput,
    PreferenceInterpretationInput,
    ProjectCreateInput,
    RequirementDraftClarifyInput,
    RequirementDraftConfirmInput,
    RequirementDraftCreateInput,
    TaskCancelInput,
    TaskCreateInput,
    TaskRetryInput,
)
from material_allocation_copilot.application.exports import review_csv
from material_allocation_copilot.application.ranking import RankedPlansResult
from material_allocation_copilot.application.services import validate_input
from material_allocation_copilot.application.workflow import WorkflowService
from material_allocation_copilot.domain import (
    MaterialItem,
    PreferenceInterpretationView,
    RequirementDraftView,
    RuleOutcome,
    SlotRequirement,
)
from material_allocation_copilot.domain.types import Identifier
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.prompts import default_prompt_registry
from material_allocation_copilot.solver.preferences import PreferenceRequest

router = APIRouter(
    prefix="/api/v1",
    responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 413, 422, 500)},
)
Caller = Annotated[Principal, Depends(get_principal)]
IdempotencyKey = Annotated[Identifier, Header(alias="Idempotency-Key")]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def workflow(request: Request) -> WorkflowService:
    return request.app.state.workflow


@router.get("/me", response_model=CallerIdentityResponse)
def get_caller_identity(caller: Caller):
    return CallerIdentityResponse(
        user_id=caller.user_id,
        roles=tuple(sorted(role.value for role in caller.roles)),
        project_ids=tuple(sorted(caller.project_ids)),
    )


def plan_response(service: WorkflowService, caller: Principal, project_id: str, plan_id: str):
    plan, approval = service.queries.get_plan(caller, project_id, plan_id)
    return PlanResponse(
        **{key: value for key, value in asdict(plan).items() if key != "payload"},
        assignments=plan.payload["assignments"],
        rule_evaluations=plan.payload["rule_evaluations"],
        approval=ApprovalResponse.model_validate(approval) if approval else None,
    )


def agent_run_response(view: AgentRunView) -> AgentRunResponse:
    run = view.run
    return AgentRunResponse(
        run_id=run.run_id,
        project_id=run.project_id,
        allocation_task_id=run.allocation_task_id,
        caller_user_id=run.caller_user_id,
        model_id=run.model_id,
        status=run.status,
        goal_hash=run.goal_hash,
        prompt_name=run.prompt_name,
        prompt_version=run.prompt_version,
        prompt_content_sha256=run.prompt_content_sha256,
        budget=run.budget_payload,
        usage=run.usage_payload,
        previous_response_id=run.previous_response_id,
        final=run.final_payload,
        error_code=run.error_code,
        created_at=run.created_at,
        updated_at=run.updated_at,
        trace=tuple(asdict(item) for item in view.trace),
        approval=asdict(view.approval) if view.approval else None,
    )


@router.post("/projects", response_model=ProjectResponse, status_code=201)
def create_project(request: Request, body: ProjectCreateInput, caller: Caller):
    return workflow(request).create_project(caller, body)


@router.get("/projects", response_model=PageResponse[ProjectResponse])
def list_projects(request: Request, caller: Caller, limit: Limit = 50, offset: Offset = 0):
    return workflow(request).queries.list_projects(caller, limit=limit, offset=offset)


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(request: Request, project_id: Identifier, caller: Caller):
    return workflow(request).queries.get_project(caller, project_id)


@router.get("/prompts", response_model=tuple[PromptResponse, ...])
def list_prompts(caller: Caller):
    del caller
    return tuple(
        {
            "name": item.name,
            "version": item.version,
            "purpose": item.purpose,
            "content_sha256": item.content_sha256,
        }
        for item in default_prompt_registry().list()
    )


@router.post(
    "/projects/{project_id}/inventory/import", response_model=InventoryResponse, status_code=201
)
def import_inventory(
    request: Request,
    project_id: Identifier,
    body: InventoryRequest,
    caller: Caller,
    idempotency_key: IdempotencyKey,
):
    caller.require_project(project_id, Role.PLANNER)
    payload = validate_input(
        InventoryImportInput,
        {"project_id": project_id, "idempotency_key": idempotency_key, **body.model_dump()},
    )
    return workflow(request).import_inventory(caller, payload)


@router.post(
    "/projects/{project_id}/inventory/treetrace", response_model=InventoryResponse, status_code=201
)
def import_treetrace(
    request: Request,
    project_id: Identifier,
    body: TreeTraceRequest,
    caller: Caller,
    idempotency_key: IdempotencyKey,
):
    caller.require_project(project_id, Role.PLANNER)
    root = request.app.state.config.treetrace_source_root
    if root is None:
        raise ApplicationError(ErrorCode.CONFIGURATION_ERROR, "TreeTrace source is not configured.")
    source = (root.resolve() / "boards.txt").resolve()
    if not source.is_relative_to(root.resolve()):
        raise ApplicationError(
            ErrorCode.CONFIGURATION_ERROR, "TreeTrace source escapes configured root."
        )
    imported = import_treetrace_boards(source, inventory_version_id=body.inventory_version_id)
    return workflow(request).import_inventory(
        caller,
        InventoryImportInput.from_treetrace(
            imported,
            project_id=project_id,
            idempotency_key=idempotency_key,
        ),
    )


@router.post(
    "/projects/{project_id}/inventory/csv", response_model=InventoryResponse, status_code=201
)
async def import_inventory_csv(
    request: Request,
    project_id: Identifier,
    inventory_version_id: Identifier,
    caller: Caller,
    idempotency_key: IdempotencyKey,
    body: Annotated[bytes, Body(media_type="text/csv")],
):
    caller.require_project(project_id, Role.PLANNER)
    payload = validate_input(
        InventoryImportInput,
        {
            "project_id": project_id,
            "inventory_version_id": inventory_version_id,
            "idempotency_key": idempotency_key,
            "source_system": "normalized_csv",
            "source_path": "upload:inventory.csv",
            "source_sha256": hashlib.sha256(body).hexdigest(),
            "imported_at": datetime.now(UTC),
            "materials": inventory_from_csv(body, inventory_version_id=inventory_version_id),
        },
    )
    return await run_in_threadpool(workflow(request).import_inventory, caller, payload)


@router.get("/projects/{project_id}/inventory/{inventory_id}", response_model=InventoryResponse)
def get_inventory(
    request: Request, project_id: Identifier, inventory_id: Identifier, caller: Caller
):
    return workflow(request).queries.get_inventory(caller, project_id, inventory_id)


@router.get(
    "/projects/{project_id}/inventory/{inventory_id}/materials",
    response_model=PageResponse[MaterialItem],
)
def list_materials(
    request: Request,
    project_id: Identifier,
    inventory_id: Identifier,
    caller: Caller,
    limit: Limit = 50,
    offset: Offset = 0,
):
    page = workflow(request).queries.list_materials(
        caller, project_id, inventory_id, limit=limit, offset=offset
    )
    return {
        "items": [item.payload for item in page.items],
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
    }


@router.post(
    "/projects/{project_id}/requirements/confirm",
    response_model=RequirementResponse,
    status_code=201,
)
def confirm_requirement(
    request: Request,
    project_id: Identifier,
    body: RequirementRequest,
    caller: Caller,
    idempotency_key: IdempotencyKey,
):
    caller.require_project(project_id, Role.REVIEWER)
    return workflow(request).confirm_requirement(
        caller,
        validate_input(
            ConfirmedRequirementInput,
            {
                "project_id": project_id,
                "idempotency_key": idempotency_key,
                **body.model_dump(),
            },
        ),
    )


@router.post(
    "/projects/{project_id}/requirement-drafts",
    response_model=RequirementDraftView,
    status_code=201,
)
def create_requirement_draft(
    request: Request,
    project_id: Identifier,
    body: RequirementDraftRequest,
    caller: Caller,
    idempotency_key: IdempotencyKey,
):
    return workflow(request).create_requirement_draft(
        caller,
        validate_input(
            RequirementDraftCreateInput,
            {
                "project_id": project_id,
                "idempotency_key": idempotency_key,
                **body.model_dump(),
            },
        ),
    )


@router.post(
    "/projects/{project_id}/preference-interpretations",
    response_model=PreferenceInterpretationView,
)
def interpret_preferences(
    request: Request,
    project_id: Identifier,
    body: PreferenceInterpretationRequest,
    caller: Caller,
):
    return workflow(request).interpret_preferences(
        caller,
        validate_input(
            PreferenceInterpretationInput,
            {"project_id": project_id, **body.model_dump()},
        ),
    )


@router.get(
    "/projects/{project_id}/requirement-drafts/{draft_id}",
    response_model=RequirementDraftView,
)
def get_requirement_draft(
    request: Request,
    project_id: Identifier,
    draft_id: Identifier,
    caller: Caller,
):
    return workflow(request).get_requirement_draft(caller, project_id, draft_id)


@router.post(
    "/projects/{project_id}/requirement-drafts/{draft_id}/clarify",
    response_model=RequirementDraftView,
)
def clarify_requirement_draft(
    request: Request,
    project_id: Identifier,
    draft_id: Identifier,
    body: RequirementClarificationRequest,
    caller: Caller,
    idempotency_key: IdempotencyKey,
):
    return workflow(request).clarify_requirement_draft(
        caller,
        validate_input(
            RequirementDraftClarifyInput,
            {
                "project_id": project_id,
                "draft_id": draft_id,
                "idempotency_key": idempotency_key,
                **body.model_dump(),
            },
        ),
    )


@router.post(
    "/projects/{project_id}/requirement-drafts/{draft_id}/confirm",
    response_model=RequirementResponse,
    status_code=201,
)
def confirm_requirement_draft(
    request: Request,
    project_id: Identifier,
    draft_id: Identifier,
    body: RequirementDraftConfirmationRequest,
    caller: Caller,
    idempotency_key: IdempotencyKey,
):
    return workflow(request).confirm_requirement_draft(
        caller,
        validate_input(
            RequirementDraftConfirmInput,
            {
                "project_id": project_id,
                "draft_id": draft_id,
                "idempotency_key": idempotency_key,
                **body.model_dump(),
            },
        ),
    )


@router.post(
    "/projects/{project_id}/slots/import", response_model=RequirementResponse, status_code=201
)
async def import_slots(
    request: Request,
    project_id: Identifier,
    requirement_id: Identifier,
    slot_requirement_version_id: Identifier,
    caller: Caller,
    idempotency_key: IdempotencyKey,
    body: Annotated[bytes, Body(media_type="text/csv")],
):
    caller.require_project(project_id, Role.REVIEWER)
    slots = slots_from_csv(
        body,
        project_id=project_id,
        slot_requirement_version_id=slot_requirement_version_id,
        confirmed_by=caller.user_id,
    )
    payload = validate_input(
        ConfirmedRequirementInput,
        {
            "project_id": project_id,
            "requirement_id": requirement_id,
            "idempotency_key": idempotency_key,
            "slots": slots,
        },
    )
    return await run_in_threadpool(workflow(request).confirm_requirement, caller, payload)


@router.get(
    "/projects/{project_id}/requirements/{requirement_id}", response_model=RequirementResponse
)
def get_requirement(
    request: Request, project_id: Identifier, requirement_id: Identifier, caller: Caller
):
    return workflow(request).queries.get_requirement(caller, project_id, requirement_id)


@router.get(
    "/projects/{project_id}/requirements/{requirement_id}/slots",
    response_model=PageResponse[SlotRequirement],
)
def list_slots(
    request: Request,
    project_id: Identifier,
    requirement_id: Identifier,
    caller: Caller,
    limit: Limit = 50,
    offset: Offset = 0,
):
    return workflow(request).queries.list_slots(
        caller, project_id, requirement_id, limit=limit, offset=offset
    )


@router.post("/projects/{project_id}/tasks", response_model=TaskResponse, status_code=201)
def create_task(
    request: Request,
    project_id: Identifier,
    body: TaskRequest,
    caller: Caller,
    idempotency_key: IdempotencyKey,
):
    service = workflow(request)
    record = service.create_task(
        caller,
        TaskCreateInput(
            project_id=project_id,
            idempotency_key=idempotency_key,
            **body.model_dump(),
        ),
    )
    return {
        **asdict(record),
        "input_snapshot_hash": service.allocations.get_input_snapshot_hash(
            record.allocation_task_id
        ),
    }


@router.get("/projects/{project_id}/tasks/{task_id}", response_model=TaskResponse)
def get_task(request: Request, project_id: Identifier, task_id: Identifier, caller: Caller):
    record, snapshot_hash = workflow(request).queries.get_task(caller, project_id, task_id)
    return {**asdict(record), "input_snapshot_hash": snapshot_hash}


@router.post("/projects/{project_id}/tasks/{task_id}/cancel", response_model=TaskResponse)
def cancel_task(
    request: Request,
    project_id: Identifier,
    task_id: Identifier,
    body: TaskCancelRequest,
    caller: Caller,
):
    service = workflow(request)
    record = service.cancel_task(
        caller,
        project_id,
        TaskCancelInput(allocation_task_id=task_id, reason=body.reason),
    )
    return {
        **asdict(record),
        "input_snapshot_hash": service.allocations.get_input_snapshot_hash(task_id),
    }


@router.post(
    "/projects/{project_id}/tasks/{task_id}/retry",
    response_model=TaskResponse,
    status_code=201,
)
def retry_task(
    request: Request,
    project_id: Identifier,
    task_id: Identifier,
    body: TaskRetryRequest,
    caller: Caller,
    idempotency_key: IdempotencyKey,
):
    service = workflow(request)
    record = service.retry_task(
        caller,
        project_id,
        TaskRetryInput(
            allocation_task_id=body.allocation_task_id,
            retry_of_task_id=task_id,
            idempotency_key=idempotency_key,
        ),
    )
    return {
        **asdict(record),
        "input_snapshot_hash": service.allocations.get_input_snapshot_hash(
            record.allocation_task_id
        ),
    }


@router.post("/projects/{project_id}/agent-runs", response_model=AgentRunResponse, status_code=201)
async def run_agent(
    request: Request,
    project_id: Identifier,
    body: AgentRunRequest,
    caller: Caller,
):
    view = await run_in_threadpool(
        workflow(request).agent_runs.run,
        caller,
        project_id,
        AgentRunInput.model_validate(body.model_dump()),
    )
    return agent_run_response(view)


@router.get("/projects/{project_id}/agent-runs/{run_id}", response_model=AgentRunResponse)
def get_agent_run(request: Request, project_id: Identifier, run_id: Identifier, caller: Caller):
    return agent_run_response(workflow(request).agent_runs.get(caller, project_id, run_id))


@router.post(
    "/projects/{project_id}/agent-runs/{run_id}/approval",
    response_model=AgentRunResponse,
)
def resolve_agent_approval(
    request: Request,
    project_id: Identifier,
    run_id: Identifier,
    body: AgentApprovalResolutionRequest,
    caller: Caller,
):
    view = workflow(request).agent_runs.resolve_approval(
        caller,
        project_id,
        run_id,
        ApprovalResolutionInput.model_validate(body.model_dump()),
    )
    return agent_run_response(view)


@router.post("/projects/{project_id}/tasks/{task_id}/validate-pair", response_model=PairResponse)
def validate_pair(
    request: Request,
    project_id: Identifier,
    task_id: Identifier,
    body: PairRequest,
    caller: Caller,
):
    results, snapshot_hash = workflow(request).queries.validate_pair(
        caller, project_id, task_id, body.material_id, body.slot_id
    )
    return PairResponse(rule_evaluations=results, input_snapshot_hash=snapshot_hash)


@router.get("/projects/{project_id}/tasks/{task_id}/candidates", response_model=CandidatePage)
def list_candidates(
    request: Request,
    project_id: Identifier,
    task_id: Identifier,
    caller: Caller,
    limit: Limit = 50,
    offset: Offset = 0,
    outcome: RuleOutcome | None = None,
):
    return workflow(request).candidates.list_pairs(
        caller,
        project_id,
        task_id,
        limit=limit,
        offset=offset,
        outcome=outcome,
    )


@router.post("/projects/{project_id}/tasks/{task_id}/solve", response_model=SolvePreview)
def solve_allocation(request: Request, project_id: Identifier, task_id: Identifier, caller: Caller):
    return workflow(request).candidates.solve(caller, project_id, task_id)


@router.post(
    "/projects/{project_id}/tasks/{task_id}/ranked-plans",
    response_model=RankedPlansResult,
)
def rank_allocation_plans(
    request: Request,
    project_id: Identifier,
    task_id: Identifier,
    body: PreferenceRequest,
    caller: Caller,
):
    return workflow(request).rankings.rank(caller, project_id, task_id, body)


@router.post(
    "/projects/{project_id}/tasks/{task_id}/plans", response_model=PlanResponse, status_code=201
)
def save_plan(
    request: Request,
    project_id: Identifier,
    task_id: Identifier,
    body: PlanRequest,
    caller: Caller,
):
    service = workflow(request)
    record = service.save_plan(
        caller,
        project_id,
        AllocationPlanInput(
            allocation_task_id=task_id,
            **body.model_dump(),
        ),
    )
    return plan_response(service, caller, project_id, record.allocation_plan_id)


@router.get(
    "/projects/{project_id}/tasks/{task_id}/plans",
    response_model=PageResponse[PlanResponse],
)
def list_plans(
    request: Request,
    project_id: Identifier,
    task_id: Identifier,
    caller: Caller,
    limit: Limit = 50,
    offset: Offset = 0,
):
    service = workflow(request)
    page = service.queries.list_plans(caller, project_id, task_id, limit=limit, offset=offset)
    return {
        "items": [
            plan_response(service, caller, project_id, item.allocation_plan_id)
            for item in page.items
        ],
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
    }


@router.get("/projects/{project_id}/plans/{plan_id}", response_model=PlanResponse)
def get_plan(request: Request, project_id: Identifier, plan_id: Identifier, caller: Caller):
    return plan_response(workflow(request), caller, project_id, plan_id)


@router.get(
    "/projects/{project_id}/plans/{plan_id}/evaluations",
    response_model=PageResponse[RuleEvaluationResponse],
)
def get_evaluations(
    request: Request,
    project_id: Identifier,
    plan_id: Identifier,
    caller: Caller,
    limit: Limit = 50,
    offset: Offset = 0,
):
    page = workflow(request).queries.list_evaluations(
        caller, project_id, plan_id, limit=limit, offset=offset
    )
    return {
        "items": [
            {
                "allocation_plan_id": item.allocation_plan_id,
                "material_id": item.material_id,
                "slot_id": item.slot_id,
                "result": item.payload,
            }
            for item in page.items
        ],
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
    }


@router.post(
    "/projects/{project_id}/plans/{plan_id}/approval",
    response_model=ApprovalResponse,
    status_code=201,
)
def decide(
    request: Request,
    project_id: Identifier,
    plan_id: Identifier,
    body: ApprovalRequest,
    caller: Caller,
    idempotency_key: IdempotencyKey,
):
    return workflow(request).decide(
        caller, project_id, plan_id, idempotency_key=idempotency_key, **body.model_dump()
    )


@router.post(
    "/projects/{project_id}/plans/{plan_id}/reservation",
    response_model=ReservationResponse,
    status_code=201,
)
def reserve_plan(
    request: Request,
    project_id: Identifier,
    plan_id: Identifier,
    body: ReservationRequest,
    caller: Caller,
    idempotency_key: IdempotencyKey,
):
    return workflow(request).reserve_plan(
        caller,
        project_id,
        plan_id,
        reservation_id=body.reservation_id,
        idempotency_key=idempotency_key,
    )


@router.get(
    "/projects/{project_id}/reservations/{reservation_id}",
    response_model=ReservationResponse,
)
def get_reservation(
    request: Request,
    project_id: Identifier,
    reservation_id: Identifier,
    caller: Caller,
):
    return workflow(request).get_reservation(caller, project_id, reservation_id)


@router.post(
    "/projects/{project_id}/reservations/{reservation_id}/release",
    response_model=ReservationResponse,
)
def release_reservation(
    request: Request,
    project_id: Identifier,
    reservation_id: Identifier,
    caller: Caller,
    idempotency_key: IdempotencyKey,
):
    return workflow(request).release_reservation(
        caller,
        project_id,
        reservation_id,
        idempotency_key=idempotency_key,
    )


@router.get(
    "/projects/{project_id}/plans/{plan_id}/export",
    response_model=PlanResponse,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def export_plan(
    request: Request,
    project_id: Identifier,
    plan_id: Identifier,
    caller: Caller,
    format: Literal["csv", "json"] = "json",
):
    service = workflow(request)
    if format == "json":
        return plan_response(service, caller, project_id, plan_id)
    plan, approval = service.queries.get_plan(caller, project_id, plan_id)
    return Response(
        review_csv(plan, approval),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="allocation-review.csv"',
            "X-Export-Purpose": "review",
        },
    )
