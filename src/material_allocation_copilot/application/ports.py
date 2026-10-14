"""Repository and unit-of-work ports consumed by application services."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from material_allocation_copilot.application.contracts import TaskStatus
from material_allocation_copilot.application.records import (
    AgentApprovalRequestRecord,
    AgentRunRecord,
    AgentTraceRecord,
    AllocationPlanRecord,
    AllocationTaskRecord,
    ApprovalRecord,
    ContextSnapshotRecord,
    InventoryVersionRecord,
    MaterialSnapshotRecord,
    ProjectRecord,
    RequirementDraftRecord,
    RequirementDraftRevisionRecord,
    RequirementRecord,
    ReservationRecord,
    RuleEvaluationRecord,
    ToolCallLogRecord,
)


class ProjectRepository(Protocol):
    def get(self, project_id: str) -> ProjectRecord | None: ...

    def add(self, record: ProjectRecord) -> None: ...

    def list_page(
        self, project_ids: frozenset[str], *, limit: int, offset: int
    ) -> tuple[tuple[ProjectRecord, ...], int]: ...


class InventoryRepository(Protocol):
    def get_version(self, inventory_version_id: str) -> InventoryVersionRecord | None: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> InventoryVersionRecord | None: ...

    def add_version(self, record: InventoryVersionRecord) -> None: ...

    def add_materials(self, records: tuple[MaterialSnapshotRecord, ...]) -> None: ...

    def count_materials(self, inventory_version_id: str) -> int: ...

    def list_materials(self, inventory_version_id: str) -> tuple[MaterialSnapshotRecord, ...]: ...

    def materials_page(
        self, inventory_version_id: str, *, limit: int, offset: int
    ) -> tuple[MaterialSnapshotRecord, ...]: ...

    def get_material(
        self, inventory_version_id: str, material_id: str
    ) -> MaterialSnapshotRecord | None: ...


class RequirementRepository(Protocol):
    def get(self, requirement_id: str) -> RequirementRecord | None: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> RequirementRecord | None: ...

    def add(self, record: RequirementRecord) -> None: ...

    def slots_page(
        self, requirement_id: str, *, limit: int, offset: int
    ) -> tuple[tuple[dict[str, object], ...], int]: ...


class RequirementDraftRepository(Protocol):
    def get(self, draft_id: str) -> RequirementDraftRecord | None: ...

    def get_revision_by_idempotency_key(
        self, idempotency_key: str
    ) -> RequirementDraftRevisionRecord | None: ...

    def add(
        self,
        record: RequirementDraftRecord,
        revision: RequirementDraftRevisionRecord,
    ) -> None: ...

    def add_revision(
        self,
        draft_id: str,
        *,
        status: str,
        updated_at: datetime,
        revision: RequirementDraftRevisionRecord,
    ) -> None: ...

    def mark_confirmed(
        self, draft_id: str, requirement_id: str, *, updated_at: datetime
    ) -> None: ...


class ContextSnapshotRepository(Protocol):
    def get(self, context_snapshot_id: str) -> ContextSnapshotRecord | None: ...

    def add(self, record: ContextSnapshotRecord) -> None: ...


class ToolCallLogRepository(Protocol):
    def get(self, tool_call_id: str) -> ToolCallLogRecord | None: ...

    def add(self, record: ToolCallLogRecord) -> None: ...

    def list_by_run(self, run_id: str) -> tuple[ToolCallLogRecord, ...]: ...


class AgentRunRepository(Protocol):
    def get(self, run_id: str) -> AgentRunRecord | None: ...

    def add(self, record: AgentRunRecord) -> None: ...

    def update(self, record: AgentRunRecord) -> None: ...

    def add_trace(self, record: AgentTraceRecord) -> None: ...

    def list_traces(self, run_id: str) -> tuple[AgentTraceRecord, ...]: ...

    def get_approval(self, run_id: str) -> AgentApprovalRequestRecord | None: ...

    def add_approval(self, record: AgentApprovalRequestRecord) -> None: ...

    def update_approval(self, record: AgentApprovalRequestRecord) -> None: ...

    def fail_interrupted(self, *, recovered_at: datetime) -> int: ...


class TaskRepository(Protocol):
    def get(self, allocation_task_id: str) -> AllocationTaskRecord | None: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> AllocationTaskRecord | None: ...

    def add(self, record: AllocationTaskRecord) -> None: ...

    def update_status(
        self,
        allocation_task_id: str,
        status: TaskStatus,
        *,
        completed_at: datetime | None = None,
        failure_code: str | None = None,
        cancellation_reason: str | None = None,
    ) -> None: ...

    def fail_interrupted(self, *, recovered_at: datetime) -> int: ...


class PlanRepository(Protocol):
    def get(self, allocation_plan_id: str) -> AllocationPlanRecord | None: ...

    def list_by_task_page(
        self, allocation_task_id: str, *, limit: int, offset: int
    ) -> tuple[tuple[AllocationPlanRecord, ...], int]: ...

    def get_by_task_version(
        self, allocation_task_id: str, plan_version: int
    ) -> AllocationPlanRecord | None: ...

    def add(
        self,
        record: AllocationPlanRecord,
        rule_evaluations: tuple[RuleEvaluationRecord, ...],
    ) -> None: ...

    def evaluations_page(
        self, allocation_plan_id: str, *, limit: int, offset: int
    ) -> tuple[tuple[RuleEvaluationRecord, ...], int]: ...


class ApprovalRepository(Protocol):
    def get(self, approval_id: str) -> ApprovalRecord | None: ...

    def get_by_plan(self, allocation_plan_id: str) -> ApprovalRecord | None: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> ApprovalRecord | None: ...

    def add(self, record: ApprovalRecord) -> None: ...


class ReservationRepository(Protocol):
    def get(self, reservation_id: str) -> ReservationRecord | None: ...

    def get_by_plan(self, allocation_plan_id: str) -> ReservationRecord | None: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> ReservationRecord | None: ...

    def get_by_release_idempotency_key(self, idempotency_key: str) -> ReservationRecord | None: ...

    def add(self, record: ReservationRecord) -> None: ...

    def release(
        self,
        reservation_id: str,
        *,
        release_idempotency_key: str,
        released_by: str,
        released_at: datetime,
    ) -> None: ...


class UnitOfWork(Protocol):
    projects: ProjectRepository
    inventories: InventoryRepository
    requirements: RequirementRepository
    requirement_drafts: RequirementDraftRepository
    context_snapshots: ContextSnapshotRepository
    tool_calls: ToolCallLogRepository
    agent_runs: AgentRunRepository
    tasks: TaskRepository
    plans: PlanRepository
    approvals: ApprovalRepository
    reservations: ReservationRepository

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
