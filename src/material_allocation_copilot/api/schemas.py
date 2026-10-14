"""HTTP request/response contracts, independent of ORM rows."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from material_allocation_copilot.application.agent_runtime import AgentBudget
from material_allocation_copilot.application.contracts import (
    AgentActionType,
    AgentApprovalStatus,
    AgentRunStatus,
    ApprovalDecision,
    ClarificationAnswer,
    ConfirmedSoftPreference,
    DraftConfirmationSlot,
    PlanTerminalStatus,
    ReservationStatus,
    RuleEvaluationInput,
    TaskStatus,
)
from material_allocation_copilot.domain import MaterialItem, RuleResult, SlotRequirement
from material_allocation_copilot.domain.types import Identifier, Sha256Hex
from material_allocation_copilot.rules import AllocationAssignment


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class InventoryRequest(RequestModel):
    inventory_version_id: Identifier
    source_system: Identifier
    source_path: str = Field(min_length=1)
    source_sha256: Sha256Hex
    imported_at: datetime
    materials: tuple[MaterialItem, ...] = Field(min_length=1, max_length=10000)


class TreeTraceRequest(RequestModel):
    inventory_version_id: Identifier


class RequirementRequest(RequestModel):
    requirement_id: Identifier
    slots: tuple[SlotRequirement, ...] = Field(min_length=1, max_length=1000)
    soft_preferences: tuple[ConfirmedSoftPreference, ...] = Field(default=(), max_length=3)


class RequirementDraftRequest(RequestModel):
    draft_id: Identifier
    natural_language: str = Field(min_length=1, max_length=12000)


class PreferenceInterpretationRequest(RequestModel):
    prompt: str = Field(min_length=1, max_length=4000)


class RequirementClarificationRequest(RequestModel):
    answers: tuple[ClarificationAnswer, ...] = Field(min_length=1, max_length=100)


class RequirementDraftConfirmationRequest(RequestModel):
    requirement_id: Identifier
    slot_requirement_version_id: Identifier
    slots: tuple[DraftConfirmationSlot, ...] = Field(min_length=1, max_length=1000)
    soft_preferences: tuple[ConfirmedSoftPreference, ...] = Field(default=(), max_length=3)


class TaskRequest(RequestModel):
    allocation_task_id: Identifier
    inventory_version_id: Identifier
    requirement_id: Identifier


class TaskCancelRequest(RequestModel):
    reason: str = Field(min_length=1, max_length=1000)


class TaskRetryRequest(RequestModel):
    allocation_task_id: Identifier


class ReservationRequest(RequestModel):
    reservation_id: Identifier


class PlanRequest(RequestModel):
    allocation_plan_id: Identifier
    plan_version: int = Field(ge=1)
    terminal_status: PlanTerminalStatus
    input_snapshot_hash: Sha256Hex
    assignments: tuple[AllocationAssignment, ...] = Field(max_length=1000)
    rule_evaluations: tuple[RuleEvaluationInput, ...] = Field(default=(), max_length=10000)


class ApprovalRequest(RequestModel):
    approval_id: Identifier
    decision: ApprovalDecision
    note: str | None = Field(default=None, max_length=2000)


class PairRequest(RequestModel):
    material_id: Identifier
    slot_id: Identifier


class AgentRunRequest(RequestModel):
    run_id: Identifier
    allocation_task_id: Identifier
    goal: str = Field(min_length=1, max_length=4_000)
    budget: AgentBudget = Field(default_factory=AgentBudget)


class AgentApprovalResolutionRequest(RequestModel):
    decision: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=2_000)


class ProjectResponse(ResponseModel):
    project_id: str
    name: str
    created_at: datetime


class CallerIdentityResponse(ResponseModel):
    user_id: str
    roles: tuple[str, ...]
    project_ids: tuple[str, ...]


class InventoryResponse(ResponseModel):
    inventory_version_id: str
    project_id: str
    idempotency_key: str
    source_system: str
    source_path: str
    source_sha256: str
    snapshot_hash: str
    item_count: int
    imported_at: datetime


class RequirementResponse(ResponseModel):
    requirement_id: str
    project_id: str
    idempotency_key: str
    slot_requirement_version_id: str
    content_hash: str
    slots: tuple[SlotRequirement, ...]
    soft_preferences: tuple[ConfirmedSoftPreference, ...]
    confirmed_by: str
    confirmed_at: datetime


class TaskResponse(ResponseModel):
    allocation_task_id: str
    project_id: str
    inventory_version_id: str
    requirement_id: str
    idempotency_key: str
    status: TaskStatus
    created_at: datetime
    completed_at: datetime | None
    updated_at: datetime
    attempt: int
    retry_of_task_id: str | None
    failure_code: str | None
    cancellation_reason: str | None
    input_snapshot_hash: str


class ReservationResponse(ResponseModel):
    reservation_id: str
    project_id: str
    allocation_plan_id: str
    inventory_version_id: str
    idempotency_key: str
    status: ReservationStatus
    material_ids: tuple[str, ...]
    reserved_by: str
    reserved_at: datetime
    release_idempotency_key: str | None
    released_by: str | None
    released_at: datetime | None


class ApprovalResponse(ResponseModel):
    approval_id: str
    allocation_plan_id: str
    idempotency_key: str
    decision: ApprovalDecision
    decided_by: str
    decided_at: datetime
    note: str | None


class PlanResponse(ResponseModel):
    allocation_plan_id: str
    allocation_task_id: str
    plan_version: int
    terminal_status: PlanTerminalStatus
    input_snapshot_hash: str
    content_hash: str
    created_at: datetime
    assignments: tuple[AllocationAssignment, ...]
    rule_evaluations: tuple[RuleEvaluationInput, ...]
    approval: ApprovalResponse | None
    execution_authorized: Literal[False] = False


class RuleEvaluationResponse(ResponseModel):
    allocation_plan_id: str
    material_id: str | None
    slot_id: str | None
    result: RuleResult


class PairResponse(ResponseModel):
    input_snapshot_hash: str
    rule_evaluations: tuple[RuleEvaluationInput, ...]


class PromptResponse(ResponseModel):
    name: str
    version: str
    purpose: str
    content_sha256: Sha256Hex


class AgentTraceResponse(ResponseModel):
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class AgentApprovalRequestResponse(ResponseModel):
    approval_request_id: str
    action: AgentActionType
    resource_id: str
    reason: str
    status: AgentApprovalStatus
    requested_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    note: str | None


class AgentRunResponse(ResponseModel):
    run_id: str
    project_id: str
    allocation_task_id: str
    caller_user_id: str
    model_id: str
    status: AgentRunStatus
    goal_hash: Sha256Hex
    prompt_name: str
    prompt_version: str
    prompt_content_sha256: Sha256Hex
    budget: dict[str, Any]
    usage: dict[str, Any]
    previous_response_id: str | None
    final: dict[str, Any] | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    trace: tuple[AgentTraceResponse, ...]
    approval: AgentApprovalRequestResponse | None


class PageResponse[Item](ResponseModel):
    items: tuple[Item, ...]
    total: int
    limit: int
    offset: int


class ErrorDetail(ResponseModel):
    code: str
    message: str
    context: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(ResponseModel):
    error: ErrorDetail
    request_id: str


class HealthResponse(ResponseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(ResponseModel):
    status: Literal["ready"] = "ready"
    database: Literal["ready", "externally_managed"]


class MetricsResponse(ResponseModel):
    requests_total: int
    status_classes: dict[str, int]
    duration_ms_average: float
    duration_ms_max: int
