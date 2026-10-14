"""SQLAlchemy implementations of application repository ports."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from material_allocation_copilot.application.contracts import (
    AgentActionType,
    AgentApprovalStatus,
    AgentRunStatus,
    ApprovalDecision,
    PlanTerminalStatus,
    ReservationStatus,
    TaskStatus,
)
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
from material_allocation_copilot.persistence.models import (
    AgentApprovalRequestRow,
    AgentRunRow,
    AgentTraceRow,
    AllocationPlanRow,
    AllocationTaskRow,
    ApprovalRow,
    ConfirmedRequirementPreferenceRow,
    ContextSnapshotRow,
    InventoryVersionRow,
    MaterialItemRow,
    ProjectRow,
    RequirementDraftRevisionRow,
    RequirementDraftRow,
    RequirementRow,
    ReservationRow,
    ReservedMaterialRow,
    RuleEvaluationRow,
    SlotRequirementRow,
    ToolCallLogRow,
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SqlAlchemyProjectRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, project_id: str) -> ProjectRecord | None:
        row = self._session.get(ProjectRow, project_id)
        if row is None:
            return None
        return ProjectRecord(row.project_id, row.name, _utc(row.created_at))

    def add(self, record: ProjectRecord) -> None:
        self._session.add(
            ProjectRow(
                project_id=record.project_id,
                name=record.name,
                created_at=_utc(record.created_at),
            )
        )

    def list_page(
        self, project_ids: frozenset[str], *, limit: int, offset: int
    ) -> tuple[tuple[ProjectRecord, ...], int]:
        condition = ProjectRow.project_id.in_(project_ids)
        total = self._session.scalar(select(func.count()).select_from(ProjectRow).where(condition))
        rows = self._session.scalars(
            select(ProjectRow)
            .where(condition)
            .order_by(ProjectRow.project_id)
            .limit(limit)
            .offset(offset)
        ).all()
        return tuple(
            ProjectRecord(row.project_id, row.name, _utc(row.created_at)) for row in rows
        ), int(total or 0)


class SqlAlchemyInventoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _record(row: InventoryVersionRow) -> InventoryVersionRecord:
        return InventoryVersionRecord(
            inventory_version_id=row.inventory_version_id,
            project_id=row.project_id,
            idempotency_key=row.idempotency_key,
            source_system=row.source_system,
            source_path=row.source_path,
            source_sha256=row.source_sha256,
            snapshot_hash=row.snapshot_hash,
            item_count=row.item_count,
            imported_at=_utc(row.imported_at),
        )

    def get_version(self, inventory_version_id: str) -> InventoryVersionRecord | None:
        row = self._session.get(InventoryVersionRow, inventory_version_id)
        return None if row is None else self._record(row)

    def get_by_idempotency_key(self, idempotency_key: str) -> InventoryVersionRecord | None:
        row = self._session.scalar(
            select(InventoryVersionRow).where(
                InventoryVersionRow.idempotency_key == idempotency_key
            )
        )
        return None if row is None else self._record(row)

    def add_version(self, record: InventoryVersionRecord) -> None:
        self._session.add(
            InventoryVersionRow(
                inventory_version_id=record.inventory_version_id,
                project_id=record.project_id,
                idempotency_key=record.idempotency_key,
                source_system=record.source_system,
                source_path=record.source_path,
                source_sha256=record.source_sha256,
                snapshot_hash=record.snapshot_hash,
                item_count=record.item_count,
                imported_at=_utc(record.imported_at),
            )
        )

    def add_materials(self, records: tuple[MaterialSnapshotRecord, ...]) -> None:
        self._session.add_all(
            MaterialItemRow(
                inventory_version_id=record.inventory_version_id,
                material_id=record.material_id,
                source_row_sha256=record.source_row_sha256,
                payload=record.payload,
            )
            for record in records
        )

    def count_materials(self, inventory_version_id: str) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(MaterialItemRow)
                .where(MaterialItemRow.inventory_version_id == inventory_version_id)
            )
            or 0
        )

    def list_materials(self, inventory_version_id: str) -> tuple[MaterialSnapshotRecord, ...]:
        rows = self._session.scalars(
            select(MaterialItemRow)
            .where(MaterialItemRow.inventory_version_id == inventory_version_id)
            .order_by(MaterialItemRow.material_id)
        ).all()
        return tuple(
            MaterialSnapshotRecord(
                row.inventory_version_id, row.material_id, row.source_row_sha256, row.payload
            )
            for row in rows
        )

    def materials_page(
        self, inventory_version_id: str, *, limit: int, offset: int
    ) -> tuple[MaterialSnapshotRecord, ...]:
        rows = self._session.scalars(
            select(MaterialItemRow)
            .where(MaterialItemRow.inventory_version_id == inventory_version_id)
            .order_by(MaterialItemRow.material_id)
            .limit(limit)
            .offset(offset)
        ).all()
        return tuple(
            MaterialSnapshotRecord(
                row.inventory_version_id, row.material_id, row.source_row_sha256, row.payload
            )
            for row in rows
        )

    def get_material(
        self, inventory_version_id: str, material_id: str
    ) -> MaterialSnapshotRecord | None:
        row = self._session.get(MaterialItemRow, (inventory_version_id, material_id))
        return (
            None
            if row is None
            else MaterialSnapshotRecord(
                row.inventory_version_id, row.material_id, row.source_row_sha256, row.payload
            )
        )


class SqlAlchemyRequirementRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _record(self, row: RequirementRow) -> RequirementRecord:
        slots = self._session.scalars(
            select(SlotRequirementRow)
            .where(SlotRequirementRow.requirement_id == row.requirement_id)
            .order_by(SlotRequirementRow.slot_id)
        ).all()
        preferences = self._session.scalars(
            select(ConfirmedRequirementPreferenceRow)
            .where(ConfirmedRequirementPreferenceRow.requirement_id == row.requirement_id)
            .order_by(ConfirmedRequirementPreferenceRow.preference_type)
        ).all()
        return RequirementRecord(
            requirement_id=row.requirement_id,
            project_id=row.project_id,
            idempotency_key=row.idempotency_key,
            slot_requirement_version_id=row.slot_requirement_version_id,
            content_hash=row.content_hash,
            slots=tuple(slot.payload for slot in slots),
            confirmed_by=row.confirmed_by,
            confirmed_at=_utc(row.confirmed_at),
            soft_preferences=tuple(item.payload for item in preferences),
        )

    def get(self, requirement_id: str) -> RequirementRecord | None:
        row = self._session.get(RequirementRow, requirement_id)
        return None if row is None else self._record(row)

    def get_by_idempotency_key(self, idempotency_key: str) -> RequirementRecord | None:
        row = self._session.scalar(
            select(RequirementRow).where(RequirementRow.idempotency_key == idempotency_key)
        )
        return None if row is None else self._record(row)

    def add(self, record: RequirementRecord) -> None:
        self._session.add(
            RequirementRow(
                requirement_id=record.requirement_id,
                project_id=record.project_id,
                idempotency_key=record.idempotency_key,
                slot_requirement_version_id=record.slot_requirement_version_id,
                content_hash=record.content_hash,
                confirmed_by=record.confirmed_by,
                confirmed_at=_utc(record.confirmed_at),
            )
        )
        self._session.flush()
        self._session.add_all(
            SlotRequirementRow(
                requirement_id=record.requirement_id,
                slot_id=slot["slot_id"],
                payload=slot,
            )
            for slot in record.slots
        )
        self._session.add_all(
            ConfirmedRequirementPreferenceRow(
                requirement_id=record.requirement_id,
                preference_type=item["preference_type"],
                payload=item,
            )
            for item in record.soft_preferences
        )
        self._session.flush()

    def slots_page(
        self, requirement_id: str, *, limit: int, offset: int
    ) -> tuple[tuple[dict[str, object], ...], int]:
        condition = SlotRequirementRow.requirement_id == requirement_id
        total = self._session.scalar(
            select(func.count()).select_from(SlotRequirementRow).where(condition)
        )
        rows = self._session.scalars(
            select(SlotRequirementRow)
            .where(condition)
            .order_by(SlotRequirementRow.slot_id)
            .limit(limit)
            .offset(offset)
        ).all()
        return tuple(row.payload for row in rows), int(total or 0)


class SqlAlchemyRequirementDraftRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _revision(row: RequirementDraftRevisionRow) -> RequirementDraftRevisionRecord:
        return RequirementDraftRevisionRecord(
            draft_id=row.draft_id,
            revision=row.revision,
            idempotency_key=row.idempotency_key,
            source_text=row.source_text,
            source_text_sha256=row.source_text_sha256,
            answers=tuple(row.answers),
            prompt_name=row.prompt_name,
            prompt_version=row.prompt_version,
            prompt_content_sha256=row.prompt_content_sha256,
            model_id=row.model_id,
            attempt_count=row.attempt_count,
            context_snapshot_ids=tuple(row.context_snapshot_ids),
            payload=row.payload,
            created_at=_utc(row.created_at),
        )

    def _record(self, row: RequirementDraftRow) -> RequirementDraftRecord:
        revision = self._session.get(
            RequirementDraftRevisionRow, (row.draft_id, row.current_revision)
        )
        if revision is None:
            raise RuntimeError("requirement draft current revision is missing")
        return RequirementDraftRecord(
            draft_id=row.draft_id,
            project_id=row.project_id,
            status=row.status,
            current_revision=row.current_revision,
            created_by=row.created_by,
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
            confirmed_requirement_id=row.confirmed_requirement_id,
            revision=self._revision(revision),
        )

    def get(self, draft_id: str) -> RequirementDraftRecord | None:
        row = self._session.get(RequirementDraftRow, draft_id)
        return None if row is None else self._record(row)

    def get_revision_by_idempotency_key(
        self, idempotency_key: str
    ) -> RequirementDraftRevisionRecord | None:
        revision = self._session.scalar(
            select(RequirementDraftRevisionRow).where(
                RequirementDraftRevisionRow.idempotency_key == idempotency_key
            )
        )
        return None if revision is None else self._revision(revision)

    @staticmethod
    def _revision_row(record: RequirementDraftRevisionRecord) -> RequirementDraftRevisionRow:
        return RequirementDraftRevisionRow(
            draft_id=record.draft_id,
            revision=record.revision,
            idempotency_key=record.idempotency_key,
            source_text=record.source_text,
            source_text_sha256=record.source_text_sha256,
            answers=list(record.answers),
            prompt_name=record.prompt_name,
            prompt_version=record.prompt_version,
            prompt_content_sha256=record.prompt_content_sha256,
            model_id=record.model_id,
            attempt_count=record.attempt_count,
            context_snapshot_ids=list(record.context_snapshot_ids),
            payload=record.payload,
            created_at=_utc(record.created_at),
        )

    def add(
        self,
        record: RequirementDraftRecord,
        revision: RequirementDraftRevisionRecord,
    ) -> None:
        self._session.add(
            RequirementDraftRow(
                draft_id=record.draft_id,
                project_id=record.project_id,
                status=record.status,
                current_revision=record.current_revision,
                created_by=record.created_by,
                created_at=_utc(record.created_at),
                updated_at=_utc(record.updated_at),
                confirmed_requirement_id=record.confirmed_requirement_id,
            )
        )
        self._session.flush()
        self._session.add(self._revision_row(revision))

    def add_revision(
        self,
        draft_id: str,
        *,
        status: str,
        updated_at: datetime,
        revision: RequirementDraftRevisionRecord,
    ) -> None:
        self._session.add(self._revision_row(revision))
        self._session.execute(
            update(RequirementDraftRow)
            .where(RequirementDraftRow.draft_id == draft_id)
            .values(
                status=status,
                current_revision=revision.revision,
                updated_at=_utc(updated_at),
            )
        )

    def mark_confirmed(self, draft_id: str, requirement_id: str, *, updated_at: datetime) -> None:
        self._session.execute(
            update(RequirementDraftRow)
            .where(RequirementDraftRow.draft_id == draft_id)
            .values(
                status="confirmed",
                confirmed_requirement_id=requirement_id,
                updated_at=_utc(updated_at),
            )
        )


class SqlAlchemyContextSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _record(row: ContextSnapshotRow) -> ContextSnapshotRecord:
        return ContextSnapshotRecord(
            context_snapshot_id=row.context_snapshot_id,
            context_hash=row.context_hash,
            project_id=row.project_id,
            draft_id=row.draft_id,
            draft_revision=row.draft_revision,
            attempt=row.attempt,
            purpose=row.purpose,
            status=row.status,
            prompt_name=row.prompt_name,
            prompt_version=row.prompt_version,
            prompt_content_sha256=row.prompt_content_sha256,
            budget_policy_version=row.budget_policy_version,
            estimated_tokens=row.estimated_tokens,
            source_count=row.source_count,
            payload=row.payload,
            created_at=_utc(row.created_at),
        )

    def get(self, context_snapshot_id: str) -> ContextSnapshotRecord | None:
        row = self._session.get(ContextSnapshotRow, context_snapshot_id)
        return None if row is None else self._record(row)

    def add(self, record: ContextSnapshotRecord) -> None:
        self._session.add(
            ContextSnapshotRow(
                context_snapshot_id=record.context_snapshot_id,
                context_hash=record.context_hash,
                project_id=record.project_id,
                draft_id=record.draft_id,
                draft_revision=record.draft_revision,
                attempt=record.attempt,
                purpose=record.purpose,
                status=record.status,
                prompt_name=record.prompt_name,
                prompt_version=record.prompt_version,
                prompt_content_sha256=record.prompt_content_sha256,
                budget_policy_version=record.budget_policy_version,
                estimated_tokens=record.estimated_tokens,
                source_count=record.source_count,
                payload=record.payload,
                created_at=_utc(record.created_at),
            )
        )


class SqlAlchemyToolCallLogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _record(row: ToolCallLogRow) -> ToolCallLogRecord:
        return ToolCallLogRecord(
            tool_call_id=row.tool_call_id,
            run_id=row.run_id,
            project_id=row.project_id,
            allocation_task_id=row.allocation_task_id,
            tool_name=row.tool_name,
            tool_version=row.tool_version,
            caller_user_id=row.caller_user_id,
            required_role=row.required_role,
            arguments_hash=row.arguments_hash,
            arguments_payload=row.arguments_payload,
            status=row.status,
            result_hash=row.result_hash,
            result_summary=row.result_summary,
            error_code=row.error_code,
            started_at=_utc(row.started_at),
            completed_at=_utc(row.completed_at),
            duration_ms=row.duration_ms,
        )

    def get(self, tool_call_id: str) -> ToolCallLogRecord | None:
        row = self._session.get(ToolCallLogRow, tool_call_id)
        return None if row is None else self._record(row)

    def add(self, record: ToolCallLogRecord) -> None:
        self._session.add(
            ToolCallLogRow(
                tool_call_id=record.tool_call_id,
                run_id=record.run_id,
                project_id=record.project_id,
                allocation_task_id=record.allocation_task_id,
                tool_name=record.tool_name,
                tool_version=record.tool_version,
                caller_user_id=record.caller_user_id,
                required_role=record.required_role,
                arguments_hash=record.arguments_hash,
                arguments_payload=record.arguments_payload,
                status=record.status,
                result_hash=record.result_hash,
                result_summary=record.result_summary,
                error_code=record.error_code,
                started_at=_utc(record.started_at),
                completed_at=_utc(record.completed_at),
                duration_ms=record.duration_ms,
            )
        )

    def list_by_run(self, run_id: str) -> tuple[ToolCallLogRecord, ...]:
        rows = self._session.scalars(
            select(ToolCallLogRow)
            .where(ToolCallLogRow.run_id == run_id)
            .order_by(ToolCallLogRow.started_at, ToolCallLogRow.tool_call_id)
        ).all()
        return tuple(self._record(row) for row in rows)


class SqlAlchemyAgentRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _run_record(row: AgentRunRow) -> AgentRunRecord:
        return AgentRunRecord(
            run_id=row.run_id,
            project_id=row.project_id,
            allocation_task_id=row.allocation_task_id,
            caller_user_id=row.caller_user_id,
            model_id=row.model_id,
            status=AgentRunStatus(row.status),
            goal_hash=row.goal_hash,
            prompt_name=row.prompt_name,
            prompt_version=row.prompt_version,
            prompt_content_sha256=row.prompt_content_sha256,
            budget_payload=row.budget_payload,
            usage_payload=row.usage_payload,
            previous_response_id=row.previous_response_id,
            final_payload=row.final_payload,
            error_code=row.error_code,
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
        )

    def get(self, run_id: str) -> AgentRunRecord | None:
        row = self._session.get(AgentRunRow, run_id)
        return None if row is None else self._run_record(row)

    def add(self, record: AgentRunRecord) -> None:
        self._session.add(
            AgentRunRow(
                run_id=record.run_id,
                project_id=record.project_id,
                allocation_task_id=record.allocation_task_id,
                caller_user_id=record.caller_user_id,
                model_id=record.model_id,
                status=record.status.value,
                goal_hash=record.goal_hash,
                prompt_name=record.prompt_name,
                prompt_version=record.prompt_version,
                prompt_content_sha256=record.prompt_content_sha256,
                budget_payload=record.budget_payload,
                usage_payload=record.usage_payload,
                previous_response_id=record.previous_response_id,
                final_payload=record.final_payload,
                error_code=record.error_code,
                created_at=_utc(record.created_at),
                updated_at=_utc(record.updated_at),
            )
        )

    def update(self, record: AgentRunRecord) -> None:
        self._session.execute(
            update(AgentRunRow)
            .where(AgentRunRow.run_id == record.run_id)
            .values(
                status=record.status.value,
                usage_payload=record.usage_payload,
                previous_response_id=record.previous_response_id,
                final_payload=record.final_payload,
                error_code=record.error_code,
                updated_at=_utc(record.updated_at),
            )
        )

    def add_trace(self, record: AgentTraceRecord) -> None:
        self._session.add(
            AgentTraceRow(
                run_id=record.run_id,
                sequence=record.sequence,
                event_type=record.event_type,
                payload=record.payload,
                created_at=_utc(record.created_at),
            )
        )

    def list_traces(self, run_id: str) -> tuple[AgentTraceRecord, ...]:
        rows = self._session.scalars(
            select(AgentTraceRow)
            .where(AgentTraceRow.run_id == run_id)
            .order_by(AgentTraceRow.sequence)
        ).all()
        return tuple(
            AgentTraceRecord(
                run_id=row.run_id,
                sequence=row.sequence,
                event_type=row.event_type,
                payload=row.payload,
                created_at=_utc(row.created_at),
            )
            for row in rows
        )

    def get_approval(self, run_id: str) -> AgentApprovalRequestRecord | None:
        row = self._session.scalar(
            select(AgentApprovalRequestRow).where(AgentApprovalRequestRow.run_id == run_id)
        )
        if row is None:
            return None
        return AgentApprovalRequestRecord(
            approval_request_id=row.approval_request_id,
            run_id=row.run_id,
            action=AgentActionType(row.action),
            resource_id=row.resource_id,
            reason=row.reason,
            status=AgentApprovalStatus(row.status),
            requested_at=_utc(row.requested_at),
            decided_by=row.decided_by,
            decided_at=_utc(row.decided_at) if row.decided_at else None,
            note=row.note,
        )

    def add_approval(self, record: AgentApprovalRequestRecord) -> None:
        self._session.add(
            AgentApprovalRequestRow(
                approval_request_id=record.approval_request_id,
                run_id=record.run_id,
                action=record.action.value,
                resource_id=record.resource_id,
                reason=record.reason,
                status=record.status.value,
                requested_at=_utc(record.requested_at),
                decided_by=record.decided_by,
                decided_at=_utc(record.decided_at) if record.decided_at else None,
                note=record.note,
            )
        )

    def update_approval(self, record: AgentApprovalRequestRecord) -> None:
        self._session.execute(
            update(AgentApprovalRequestRow)
            .where(AgentApprovalRequestRow.approval_request_id == record.approval_request_id)
            .values(
                status=record.status.value,
                decided_by=record.decided_by,
                decided_at=_utc(record.decided_at) if record.decided_at else None,
                note=record.note,
            )
        )

    def fail_interrupted(self, *, recovered_at: datetime) -> int:
        result = self._session.execute(
            update(AgentRunRow)
            .where(AgentRunRow.status == AgentRunStatus.RUNNING.value)
            .values(
                status=AgentRunStatus.TOOL_ERROR.value,
                error_code="interrupted_by_restart",
                updated_at=_utc(recovered_at),
            )
        )
        return int(result.rowcount or 0)


class SqlAlchemyTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _record(row: AllocationTaskRow) -> AllocationTaskRecord:
        return AllocationTaskRecord(
            allocation_task_id=row.allocation_task_id,
            project_id=row.project_id,
            inventory_version_id=row.inventory_version_id,
            requirement_id=row.requirement_id,
            idempotency_key=row.idempotency_key,
            status=TaskStatus(row.status),
            created_at=_utc(row.created_at),
            completed_at=_utc(row.completed_at) if row.completed_at else None,
            updated_at=_utc(row.updated_at),
            attempt=row.attempt,
            retry_of_task_id=row.retry_of_task_id,
            failure_code=row.failure_code,
            cancellation_reason=row.cancellation_reason,
        )

    def get(self, allocation_task_id: str) -> AllocationTaskRecord | None:
        row = self._session.get(AllocationTaskRow, allocation_task_id)
        return None if row is None else self._record(row)

    def get_by_idempotency_key(self, idempotency_key: str) -> AllocationTaskRecord | None:
        row = self._session.scalar(
            select(AllocationTaskRow).where(AllocationTaskRow.idempotency_key == idempotency_key)
        )
        return None if row is None else self._record(row)

    def add(self, record: AllocationTaskRecord) -> None:
        self._session.add(
            AllocationTaskRow(
                allocation_task_id=record.allocation_task_id,
                project_id=record.project_id,
                inventory_version_id=record.inventory_version_id,
                requirement_id=record.requirement_id,
                idempotency_key=record.idempotency_key,
                status=record.status.value,
                created_at=_utc(record.created_at),
                updated_at=_utc(record.updated_at or record.created_at),
                completed_at=_utc(record.completed_at) if record.completed_at else None,
                attempt=record.attempt,
                retry_of_task_id=record.retry_of_task_id,
                failure_code=record.failure_code,
                cancellation_reason=record.cancellation_reason,
            )
        )

    def update_status(
        self,
        allocation_task_id: str,
        status: TaskStatus,
        *,
        completed_at: datetime | None = None,
        failure_code: str | None = None,
        cancellation_reason: str | None = None,
    ) -> None:
        now = _utc(completed_at) if completed_at else datetime.now(UTC)
        self._session.execute(
            update(AllocationTaskRow)
            .where(AllocationTaskRow.allocation_task_id == allocation_task_id)
            .values(
                status=status.value,
                completed_at=_utc(completed_at) if completed_at else None,
                updated_at=now,
                failure_code=failure_code,
                cancellation_reason=cancellation_reason,
            )
        )

    def fail_interrupted(self, *, recovered_at: datetime) -> int:
        result = self._session.execute(
            update(AllocationTaskRow)
            .where(AllocationTaskRow.status == TaskStatus.RUNNING.value)
            .values(
                status=TaskStatus.FAILED.value,
                updated_at=_utc(recovered_at),
                completed_at=_utc(recovered_at),
                failure_code="interrupted_by_restart",
            )
        )
        return int(result.rowcount or 0)


class SqlAlchemyPlanRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _record(row: AllocationPlanRow) -> AllocationPlanRecord:
        return AllocationPlanRecord(
            allocation_plan_id=row.allocation_plan_id,
            allocation_task_id=row.allocation_task_id,
            plan_version=row.plan_version,
            terminal_status=PlanTerminalStatus(row.terminal_status),
            input_snapshot_hash=row.input_snapshot_hash,
            content_hash=row.content_hash,
            payload=row.payload,
            created_at=_utc(row.created_at),
        )

    def get(self, allocation_plan_id: str) -> AllocationPlanRecord | None:
        row = self._session.get(AllocationPlanRow, allocation_plan_id)
        return None if row is None else self._record(row)

    def list_by_task_page(
        self, allocation_task_id: str, *, limit: int, offset: int
    ) -> tuple[tuple[AllocationPlanRecord, ...], int]:
        condition = AllocationPlanRow.allocation_task_id == allocation_task_id
        total = self._session.scalar(
            select(func.count()).select_from(AllocationPlanRow).where(condition)
        )
        rows = self._session.scalars(
            select(AllocationPlanRow)
            .where(condition)
            .order_by(AllocationPlanRow.plan_version.desc(), AllocationPlanRow.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return tuple(self._record(row) for row in rows), int(total or 0)

    def get_by_task_version(
        self, allocation_task_id: str, plan_version: int
    ) -> AllocationPlanRecord | None:
        row = self._session.scalar(
            select(AllocationPlanRow).where(
                AllocationPlanRow.allocation_task_id == allocation_task_id,
                AllocationPlanRow.plan_version == plan_version,
            )
        )
        return None if row is None else self._record(row)

    def add(
        self,
        record: AllocationPlanRecord,
        rule_evaluations: tuple[RuleEvaluationRecord, ...],
    ) -> None:
        self._session.add(
            AllocationPlanRow(
                allocation_plan_id=record.allocation_plan_id,
                allocation_task_id=record.allocation_task_id,
                plan_version=record.plan_version,
                terminal_status=record.terminal_status.value,
                input_snapshot_hash=record.input_snapshot_hash,
                content_hash=record.content_hash,
                payload=record.payload,
                created_at=_utc(record.created_at),
            )
        )
        self._session.add_all(
            RuleEvaluationRow(
                allocation_plan_id=item.allocation_plan_id,
                material_id=item.material_id,
                slot_id=item.slot_id,
                rule_id=item.rule_id,
                rule_version=item.rule_version,
                outcome=item.outcome,
                severity=item.severity,
                payload=item.payload,
            )
            for item in rule_evaluations
        )

    def evaluations_page(
        self, allocation_plan_id: str, *, limit: int, offset: int
    ) -> tuple[tuple[RuleEvaluationRecord, ...], int]:
        condition = RuleEvaluationRow.allocation_plan_id == allocation_plan_id
        total = self._session.scalar(
            select(func.count()).select_from(RuleEvaluationRow).where(condition)
        )
        rows = self._session.scalars(
            select(RuleEvaluationRow)
            .where(condition)
            .order_by(RuleEvaluationRow.rule_evaluation_id)
            .limit(limit)
            .offset(offset)
        ).all()
        return tuple(
            RuleEvaluationRecord(
                row.allocation_plan_id,
                row.material_id,
                row.slot_id,
                row.rule_id,
                row.rule_version,
                row.outcome,
                row.severity,
                row.payload,
            )
            for row in rows
        ), int(total or 0)


class SqlAlchemyApprovalRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _record(row: ApprovalRow) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=row.approval_id,
            allocation_plan_id=row.allocation_plan_id,
            idempotency_key=row.idempotency_key,
            decision=ApprovalDecision(row.decision),
            decided_by=row.decided_by,
            decided_at=_utc(row.decided_at),
            note=row.note,
        )

    def get(self, approval_id: str) -> ApprovalRecord | None:
        row = self._session.get(ApprovalRow, approval_id)
        return None if row is None else self._record(row)

    def get_by_plan(self, allocation_plan_id: str) -> ApprovalRecord | None:
        row = self._session.scalar(
            select(ApprovalRow).where(ApprovalRow.allocation_plan_id == allocation_plan_id)
        )
        return None if row is None else self._record(row)

    def get_by_idempotency_key(self, idempotency_key: str) -> ApprovalRecord | None:
        row = self._session.scalar(
            select(ApprovalRow).where(ApprovalRow.idempotency_key == idempotency_key)
        )
        return None if row is None else self._record(row)

    def add(self, record: ApprovalRecord) -> None:
        self._session.add(
            ApprovalRow(
                approval_id=record.approval_id,
                allocation_plan_id=record.allocation_plan_id,
                idempotency_key=record.idempotency_key,
                decision=record.decision.value,
                decided_by=record.decided_by,
                decided_at=_utc(record.decided_at),
                note=record.note,
            )
        )


class SqlAlchemyReservationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _record(self, row: ReservationRow) -> ReservationRecord:
        material_ids = tuple(
            self._session.scalars(
                select(ReservedMaterialRow.material_id)
                .where(ReservedMaterialRow.reservation_id == row.reservation_id)
                .order_by(ReservedMaterialRow.material_id)
            ).all()
        )
        return ReservationRecord(
            reservation_id=row.reservation_id,
            project_id=row.project_id,
            allocation_plan_id=row.allocation_plan_id,
            inventory_version_id=row.inventory_version_id,
            idempotency_key=row.idempotency_key,
            status=ReservationStatus(row.status),
            material_ids=material_ids,
            reserved_by=row.reserved_by,
            reserved_at=_utc(row.reserved_at),
            release_idempotency_key=row.release_idempotency_key,
            released_by=row.released_by,
            released_at=_utc(row.released_at) if row.released_at else None,
        )

    def get(self, reservation_id: str) -> ReservationRecord | None:
        row = self._session.get(ReservationRow, reservation_id)
        return None if row is None else self._record(row)

    def get_by_plan(self, allocation_plan_id: str) -> ReservationRecord | None:
        row = self._session.scalar(
            select(ReservationRow).where(ReservationRow.allocation_plan_id == allocation_plan_id)
        )
        return None if row is None else self._record(row)

    def get_by_idempotency_key(self, idempotency_key: str) -> ReservationRecord | None:
        row = self._session.scalar(
            select(ReservationRow).where(ReservationRow.idempotency_key == idempotency_key)
        )
        return None if row is None else self._record(row)

    def get_by_release_idempotency_key(self, idempotency_key: str) -> ReservationRecord | None:
        row = self._session.scalar(
            select(ReservationRow).where(ReservationRow.release_idempotency_key == idempotency_key)
        )
        return None if row is None else self._record(row)

    def add(self, record: ReservationRecord) -> None:
        self._session.add(
            ReservationRow(
                reservation_id=record.reservation_id,
                project_id=record.project_id,
                allocation_plan_id=record.allocation_plan_id,
                inventory_version_id=record.inventory_version_id,
                idempotency_key=record.idempotency_key,
                status=record.status.value,
                reserved_by=record.reserved_by,
                reserved_at=_utc(record.reserved_at),
            )
        )
        self._session.add_all(
            ReservedMaterialRow(
                reservation_id=record.reservation_id,
                inventory_version_id=record.inventory_version_id,
                material_id=material_id,
                status=ReservationStatus.ACTIVE.value,
            )
            for material_id in record.material_ids
        )

    def release(
        self,
        reservation_id: str,
        *,
        release_idempotency_key: str,
        released_by: str,
        released_at: datetime,
    ) -> None:
        timestamp = _utc(released_at)
        self._session.execute(
            update(ReservationRow)
            .where(ReservationRow.reservation_id == reservation_id)
            .values(
                status=ReservationStatus.RELEASED.value,
                release_idempotency_key=release_idempotency_key,
                released_by=released_by,
                released_at=timestamp,
            )
        )
        self._session.execute(
            update(ReservedMaterialRow)
            .where(ReservedMaterialRow.reservation_id == reservation_id)
            .values(status=ReservationStatus.RELEASED.value, released_at=timestamp)
        )
