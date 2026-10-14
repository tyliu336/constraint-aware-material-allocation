"""SQLAlchemy product persistence models; no paper-experiment state is present."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InventoryVersionRow(Base):
    __tablename__ = "inventory_versions"

    inventory_version_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    source_system: Mapped[str] = mapped_column(String(128), nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("item_count > 0", name="ck_inventory_versions_item_count_positive"),
    )


class MaterialItemRow(Base):
    __tablename__ = "material_items"

    inventory_version_id: Mapped[str] = mapped_column(
        ForeignKey("inventory_versions.inventory_version_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    material_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_row_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class RequirementRow(Base):
    __tablename__ = "requirements"

    requirement_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    slot_requirement_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SlotRequirementRow(Base):
    __tablename__ = "slot_requirements"

    requirement_id: Mapped[str] = mapped_column(
        ForeignKey("requirements.requirement_id", ondelete="RESTRICT"), primary_key=True
    )
    slot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ConfirmedRequirementPreferenceRow(Base):
    __tablename__ = "confirmed_requirement_preferences"

    requirement_id: Mapped[str] = mapped_column(
        ForeignKey("requirements.requirement_id", ondelete="RESTRICT"), primary_key=True
    )
    preference_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class RequirementDraftRow(Base):
    __tablename__ = "requirement_drafts"

    draft_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_requirement_id: Mapped[str | None] = mapped_column(
        ForeignKey("requirements.requirement_id", ondelete="RESTRICT"), unique=True
    )

    __table_args__ = (
        CheckConstraint("current_revision >= 1", name="ck_requirement_drafts_revision_positive"),
        CheckConstraint(
            "status IN ('needs_clarification','ready_for_confirmation','parse_failed','confirmed')",
            name="ck_requirement_drafts_status",
        ),
    )


class RequirementDraftRevisionRow(Base):
    __tablename__ = "requirement_draft_revisions"

    draft_id: Mapped[str] = mapped_column(
        ForeignKey("requirement_drafts.draft_id", ondelete="RESTRICT"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    answers: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    prompt_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    context_snapshot_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_requirement_draft_revisions_positive"),
        CheckConstraint(
            "attempt_count >= 1 AND attempt_count <= 2",
            name="ck_requirement_draft_revisions_attempts",
        ),
    )


class ContextSnapshotRow(Base):
    __tablename__ = "context_snapshots"

    context_snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    draft_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    estimated_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("draft_revision >= 1", name="ck_context_snapshots_revision_positive"),
        CheckConstraint("attempt >= 1 AND attempt <= 2", name="ck_context_snapshots_attempt"),
        CheckConstraint("estimated_tokens >= 0", name="ck_context_snapshots_tokens"),
        CheckConstraint("source_count >= 0", name="ck_context_snapshots_sources"),
        CheckConstraint(
            "status IN ('ready','budget_exceeded','stale_input','conflicting_input')",
            name="ck_context_snapshots_status",
        ),
    )


class ToolCallLogRow(Base):
    __tablename__ = "tool_call_logs"

    tool_call_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    allocation_task_id: Mapped[str | None] = mapped_column(String(128), index=True)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tool_version: Mapped[str] = mapped_column(String(64), nullable=False)
    caller_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    required_role: Mapped[str | None] = mapped_column(String(32))
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_hash: Mapped[str | None] = mapped_column(String(64))
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('succeeded','failed','timeout')", name="ck_tool_call_logs_status"
        ),
        CheckConstraint("duration_ms >= 0", name="ck_tool_call_logs_duration"),
    )


class AllocationTaskRow(Base):
    __tablename__ = "allocation_tasks"

    allocation_task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    inventory_version_id: Mapped[str] = mapped_column(
        ForeignKey("inventory_versions.inventory_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    requirement_id: Mapped[str] = mapped_column(
        ForeignKey("requirements.requirement_id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    retry_of_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("allocation_tasks.allocation_task_id", ondelete="RESTRICT"), index=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(64))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed','cancelled')",
            name="ck_allocation_tasks_status",
        ),
        CheckConstraint("attempt >= 1 AND attempt <= 3", name="ck_allocation_tasks_attempt"),
    )


class AgentRunRow(Base):
    __tablename__ = "agent_runs"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    allocation_task_id: Mapped[str] = mapped_column(
        ForeignKey("allocation_tasks.allocation_task_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    caller_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    goal_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    usage_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    previous_response_id: Mapped[str | None] = mapped_column(String(200))
    final_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('running','awaiting_approval','success','needs_clarification',"
            "'no_solution','tool_error','budget_exhausted','approval_granted',"
            "'approval_rejected')",
            name="ck_agent_runs_status",
        ),
    )


class AgentTraceRow(Base):
    __tablename__ = "agent_traces"

    agent_trace_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_trace_run_sequence"),
        CheckConstraint("sequence >= 1", name="ck_agent_trace_sequence_positive"),
    )


class AgentApprovalRequestRow(Base):
    __tablename__ = "agent_approval_requests"

    approval_request_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(128))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "action IN ('save_allocation_plan','reserve_inventory','approve_plan')",
            name="ck_agent_approval_requests_action",
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected')",
            name="ck_agent_approval_requests_status",
        ),
    )


class AllocationPlanRow(Base):
    __tablename__ = "allocation_plans"

    allocation_plan_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    allocation_task_id: Mapped[str] = mapped_column(
        ForeignKey("allocation_tasks.allocation_task_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    terminal_status: Mapped[str] = mapped_column(String(30), nullable=False)
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "allocation_task_id", "plan_version", name="uq_allocation_plan_task_version"
        ),
        CheckConstraint("plan_version >= 1", name="ck_allocation_plans_version_positive"),
        CheckConstraint(
            "terminal_status IN "
            "('complete_feasible','requires_review','infeasible','input_invalid','budget_exhausted')",
            name="ck_allocation_plans_terminal_status",
        ),
    )


class RuleEvaluationRow(Base):
    __tablename__ = "rule_evaluations"

    rule_evaluation_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    allocation_plan_id: Mapped[str] = mapped_column(
        ForeignKey("allocation_plans.allocation_plan_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    material_id: Mapped[str | None] = mapped_column(String(128))
    slot_id: Mapped[str | None] = mapped_column(String(128))
    rule_id: Mapped[str] = mapped_column(String(128), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    severity: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('pass','fail','not_evaluable')",
            name="ck_rule_evaluations_outcome",
        ),
        CheckConstraint(
            "severity IN ('blocking','advisory')",
            name="ck_rule_evaluations_severity",
        ),
    )


class ApprovalRow(Base):
    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    allocation_plan_id: Mapped[str] = mapped_column(
        ForeignKey("allocation_plans.allocation_plan_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(128), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("decision IN ('approved','rejected')", name="ck_approvals_decision"),
    )


class ReservationRow(Base):
    __tablename__ = "inventory_reservations"

    reservation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    allocation_plan_id: Mapped[str] = mapped_column(
        ForeignKey("allocation_plans.allocation_plan_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    inventory_version_id: Mapped[str] = mapped_column(
        ForeignKey("inventory_versions.inventory_version_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    reserved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    release_idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    released_by: Mapped[str | None] = mapped_column(String(128))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('active','released')", name="ck_inventory_reservations_status"),
    )


class ReservedMaterialRow(Base):
    __tablename__ = "reserved_materials"

    reservation_id: Mapped[str] = mapped_column(
        ForeignKey("inventory_reservations.reservation_id", ondelete="RESTRICT"), primary_key=True
    )
    inventory_version_id: Mapped[str] = mapped_column(String(128), nullable=False)
    material_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('active','released')", name="ck_reserved_materials_status"),
        ForeignKeyConstraint(
            ["inventory_version_id", "material_id"],
            ["material_items.inventory_version_id", "material_items.material_id"],
            ondelete="RESTRICT",
        ),
        Index(
            "uq_reserved_material_active",
            "inventory_version_id",
            "material_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )
