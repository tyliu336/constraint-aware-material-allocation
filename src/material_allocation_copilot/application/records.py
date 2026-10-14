"""Persistence-neutral records returned by repositories and services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from material_allocation_copilot.application.contracts import (
    AgentActionType,
    AgentApprovalStatus,
    AgentRunStatus,
    ApprovalDecision,
    PlanTerminalStatus,
    ReservationStatus,
    TaskStatus,
)


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    project_id: str
    name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class InventoryVersionRecord:
    inventory_version_id: str
    project_id: str
    idempotency_key: str
    source_system: str
    source_path: str
    source_sha256: str
    snapshot_hash: str
    item_count: int
    imported_at: datetime


@dataclass(frozen=True, slots=True)
class MaterialSnapshotRecord:
    inventory_version_id: str
    material_id: str
    source_row_sha256: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RequirementRecord:
    requirement_id: str
    project_id: str
    idempotency_key: str
    slot_requirement_version_id: str
    content_hash: str
    slots: tuple[dict[str, Any], ...]
    confirmed_by: str
    confirmed_at: datetime
    soft_preferences: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class RequirementDraftRevisionRecord:
    draft_id: str
    revision: int
    idempotency_key: str
    source_text: str
    source_text_sha256: str
    answers: tuple[dict[str, str], ...]
    prompt_name: str
    prompt_version: str
    prompt_content_sha256: str
    model_id: str
    attempt_count: int
    context_snapshot_ids: tuple[str, ...]
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RequirementDraftRecord:
    draft_id: str
    project_id: str
    status: str
    current_revision: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    confirmed_requirement_id: str | None
    revision: RequirementDraftRevisionRecord


@dataclass(frozen=True, slots=True)
class ContextSnapshotRecord:
    context_snapshot_id: str
    context_hash: str
    project_id: str
    draft_id: str
    draft_revision: int
    attempt: int
    purpose: str
    status: str
    prompt_name: str
    prompt_version: str
    prompt_content_sha256: str
    budget_policy_version: str
    estimated_tokens: int
    source_count: int
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ToolCallLogRecord:
    tool_call_id: str
    run_id: str
    project_id: str
    allocation_task_id: str | None
    tool_name: str
    tool_version: str
    caller_user_id: str
    required_role: str | None
    arguments_hash: str
    arguments_payload: dict[str, Any]
    status: str
    result_hash: str | None
    result_summary: dict[str, Any] | None
    error_code: str | None
    started_at: datetime
    completed_at: datetime
    duration_ms: int


@dataclass(frozen=True, slots=True)
class AgentRunRecord:
    run_id: str
    project_id: str
    allocation_task_id: str
    caller_user_id: str
    model_id: str
    status: AgentRunStatus
    goal_hash: str
    prompt_name: str
    prompt_version: str
    prompt_content_sha256: str
    budget_payload: dict[str, Any]
    usage_payload: dict[str, Any]
    previous_response_id: str | None
    final_payload: dict[str, Any] | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AgentTraceRecord:
    run_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AgentApprovalRequestRecord:
    approval_request_id: str
    run_id: str
    action: AgentActionType
    resource_id: str
    reason: str
    status: AgentApprovalStatus
    requested_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class AllocationTaskRecord:
    allocation_task_id: str
    project_id: str
    inventory_version_id: str
    requirement_id: str
    idempotency_key: str
    status: TaskStatus
    created_at: datetime
    completed_at: datetime | None = None
    updated_at: datetime | None = None
    attempt: int = 1
    retry_of_task_id: str | None = None
    failure_code: str | None = None
    cancellation_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReservationRecord:
    reservation_id: str
    project_id: str
    allocation_plan_id: str
    inventory_version_id: str
    idempotency_key: str
    status: ReservationStatus
    material_ids: tuple[str, ...]
    reserved_by: str
    reserved_at: datetime
    release_idempotency_key: str | None = None
    released_by: str | None = None
    released_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AllocationPlanRecord:
    allocation_plan_id: str
    allocation_task_id: str
    plan_version: int
    terminal_status: PlanTerminalStatus
    input_snapshot_hash: str
    content_hash: str
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RuleEvaluationRecord:
    allocation_plan_id: str
    material_id: str | None
    slot_id: str | None
    rule_id: str
    rule_version: str
    outcome: str
    severity: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    approval_id: str
    allocation_plan_id: str
    idempotency_key: str
    decision: ApprovalDecision
    decided_by: str
    decided_at: datetime
    note: str | None
