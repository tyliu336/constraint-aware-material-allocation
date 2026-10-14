"""Schema-bound requirement interpretation with deterministic clarification guards."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ValidationError

from material_allocation_copilot.application.context_builder import (
    ContextBuilder,
    ContextBuildStatus,
    ContextPurpose,
    ContextSnapshot,
    ContextSource,
    ContextTrust,
)
from material_allocation_copilot.application.contracts import (
    RequirementDraftClarifyInput,
    RequirementDraftConfirmInput,
    RequirementDraftCreateInput,
)
from material_allocation_copilot.application.hashing import canonical_hash
from material_allocation_copilot.application.records import (
    ContextSnapshotRecord,
    RequirementDraftRecord,
    RequirementDraftRevisionRecord,
    RequirementRecord,
)
from material_allocation_copilot.application.services import UnitOfWorkFactory, validate_input
from material_allocation_copilot.domain import (
    ClarificationQuestion,
    DraftIssue,
    DraftIssueCode,
    ModelRequirementInterpretation,
    RequirementDraftStatus,
    RequirementDraftView,
    SlotRequirement,
)
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.prompts import PromptDefinition, PromptRegistry

MAX_MODEL_ATTEMPTS = 2


class ModelOutputError(Exception):
    """A bounded, user-safe model output failure."""


class ModelInvocationError(Exception):
    """The configured provider could not complete a model request."""


class StructuredRequirementModel(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def available(self) -> bool: ...

    def generate_structured(
        self,
        *,
        prompt: PromptDefinition,
        user_input: str,
        output_type: type[BaseModel],
    ) -> object: ...


class UnavailableRequirementModel:
    model_id = "unconfigured"
    available = False

    def generate_structured(
        self,
        *,
        prompt: PromptDefinition,
        user_input: str,
        output_type: type[BaseModel],
    ) -> object:
        del prompt, user_input, output_type
        raise ModelOutputError("requirement model is not configured")


_HARD_RULE_CHANGE = re.compile(
    r"(?:忽略|跳过|绕过|取消|放宽|降低).{0,16}(?:硬规则|硬约束|承重|强度|等级|安全)"
    r"|(?:ignore|skip|bypass|relax|lower).{0,24}(?:hard rule|constraint|strength|grade|safety)",
    re.IGNORECASE,
)
_AMBIGUOUS_RANGE = re.compile(
    r"(?<![\d.])\d+(?:\.\d+)?\s*(?:-|–|—|到|至|~)\s*\d+(?:\.\d+)?(?![\d.])"
)

_REQUIRED_SLOT_FIELDS = (
    ("slot_id", "text"),
    ("slot_role", "text"),
    ("clear_span_mm", "decimal"),
    ("end_allowance_mm", "decimal"),
    ("min_section_width_mm", "decimal"),
    ("min_section_height_mm", "decimal"),
    ("allow_section_rotation", "boolean"),
    ("orientation_requirement", "choice"),
    ("rule_set_version", "text"),
    ("requirement_source_ref", "text"),
)


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _auto_issue(
    issue_id: str,
    code: DraftIssueCode,
    field_path: str | None,
    message: str,
    *,
    evidence_quote: str | None = None,
) -> DraftIssue:
    return DraftIssue(
        issue_id=issue_id,
        code=code,
        severity="blocking",
        field_path=field_path,
        message=message,
        evidence_quote=evidence_quote,
    )


def _auto_question(
    question_id: str,
    field_path: str,
    answer_kind: str,
    question: str,
) -> ClarificationQuestion:
    options: tuple[str, ...] = ()
    if field_path.endswith("orientation_requirement"):
        options = ("fixed", "rotatable", "any")
    return ClarificationQuestion(
        question_id=question_id,
        field_path=field_path,
        question=question,
        answer_kind=answer_kind,
        options=options,
        required=True,
    )


def normalize_interpretation(
    source_text: str, interpretation: ModelRequirementInterpretation
) -> ModelRequirementInterpretation:
    always_blocking = {
        DraftIssueCode.AMBIGUOUS_SCOPE,
        DraftIssueCode.MISSING_REQUIRED_FIELD,
        DraftIssueCode.CONFLICTING_PREFERENCE,
        DraftIssueCode.SUSPECTED_HARD_RULE_CHANGE,
    }
    issues = {
        item.issue_id: (
            item.model_copy(update={"severity": "blocking"})
            if item.code in always_blocking
            else item
        )
        for item in interpretation.issues
    }
    questions = {item.question_id: item for item in interpretation.clarification_questions}

    if not interpretation.slots:
        issues["AUTO-NO-SLOTS"] = _auto_issue(
            "AUTO-NO-SLOTS",
            DraftIssueCode.MISSING_REQUIRED_FIELD,
            "slots",
            "At least one target slot must be described before confirmation.",
        )
        questions["AUTO-NO-SLOTS"] = _auto_question(
            "AUTO-NO-SLOTS",
            "slots",
            "text",
            "Which target slots or positions need materials?",
        )

    for index, slot in enumerate(interpretation.slots, start=1):
        for field_name, answer_kind in _REQUIRED_SLOT_FIELDS:
            if getattr(slot, field_name) is not None:
                continue
            identity = f"AUTO-S{index:03d}-{field_name}"
            field_path = f"slots[{index - 1}].{field_name}"
            issues[identity] = _auto_issue(
                identity,
                DraftIssueCode.MISSING_REQUIRED_FIELD,
                field_path,
                f"{field_name} is required before this slot can be confirmed.",
            )
            questions[identity] = _auto_question(
                identity,
                field_path,
                answer_kind,
                f"What is the confirmed value for {field_name} on slot {index}?",
            )
        if slot.minimum_strength_grade is None and slot.capacity_requirement_ref is None:
            identity = f"AUTO-S{index:03d}-structural_basis"
            field_path = f"slots[{index - 1}].structural_basis"
            issues[identity] = _auto_issue(
                identity,
                DraftIssueCode.MISSING_REQUIRED_FIELD,
                field_path,
                "A confirmed strength grade or capacity requirement reference is required.",
            )
            questions[identity] = _auto_question(
                identity,
                field_path,
                "text",
                f"What approved strength grade or capacity reference applies to slot {index}?",
            )

    seen_preferences: dict[str, object] = {}
    for index, preference in enumerate(interpretation.soft_preferences, start=1):
        previous = seen_preferences.get(preference.preference_type)
        if previous is not None:
            identity = f"AUTO-PREF-{preference.preference_type}"
            issues[identity] = _auto_issue(
                identity,
                DraftIssueCode.CONFLICTING_PREFERENCE,
                "soft_preferences",
                f"Preference {preference.preference_type} has conflicting definitions.",
            )
            questions[identity] = _auto_question(
                identity,
                "soft_preferences",
                "text",
                f"Which definition and weight should apply to {preference.preference_type}?",
            )
        seen_preferences[preference.preference_type] = preference
        if preference.requested_weight is None:
            identity = f"AUTO-PREF-{index:03d}-requested_weight"
            field_path = f"soft_preferences[{index - 1}].requested_weight"
            issues[identity] = _auto_issue(
                identity,
                DraftIssueCode.MISSING_REQUIRED_FIELD,
                field_path,
                "A positive preference weight is required before confirmation.",
            )
            questions[identity] = _auto_question(
                identity,
                field_path,
                "decimal",
                f"What positive weight should apply to {preference.preference_type}?",
            )

    hard_match = _HARD_RULE_CHANGE.search(source_text)
    if hard_match:
        issues["AUTO-HARD-RULE-CHANGE"] = _auto_issue(
            "AUTO-HARD-RULE-CHANGE",
            DraftIssueCode.SUSPECTED_HARD_RULE_CHANGE,
            "natural_language",
            "The request appears to ask for a hard-rule reduction or bypass; it cannot be applied.",
            evidence_quote=hard_match.group(0),
        )
        questions["AUTO-HARD-RULE-CHANGE"] = _auto_question(
            "AUTO-HARD-RULE-CHANGE",
            "natural_language",
            "text",
            "Provide the approved rule-set reference instead of changing a hard rule in "
            "conversation.",
        )

    range_match = _AMBIGUOUS_RANGE.search(source_text)
    if range_match:
        issues["AUTO-AMBIGUOUS-RANGE"] = _auto_issue(
            "AUTO-AMBIGUOUS-RANGE",
            DraftIssueCode.AMBIGUOUS_SCOPE,
            "natural_language",
            "A numeric range was found where an exact confirmed value may be required.",
            evidence_quote=range_match.group(0),
        )
        questions["AUTO-AMBIGUOUS-RANGE"] = _auto_question(
            "AUTO-AMBIGUOUS-RANGE",
            "natural_language",
            "decimal",
            "Which exact value from the stated range should be used?",
        )

    return interpretation.model_copy(
        update={
            "issues": tuple(issues[key] for key in sorted(issues)),
            "clarification_questions": tuple(questions[key] for key in sorted(questions)),
        }
    )


def _status_for(interpretation: ModelRequirementInterpretation) -> RequirementDraftStatus:
    blocking = any(item.severity == "blocking" for item in interpretation.issues)
    required_questions = any(item.required for item in interpretation.clarification_questions)
    if blocking or required_questions:
        return RequirementDraftStatus.NEEDS_CLARIFICATION
    return RequirementDraftStatus.READY_FOR_CONFIRMATION


class RequirementDraftService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        model: StructuredRequirementModel,
        prompts: PromptRegistry,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._model = model
        self._prompts = prompts
        self._context_builder = context_builder or ContextBuilder()

    def _generate(
        self,
        prompt: PromptDefinition,
        *,
        project_id: str,
        purpose: ContextPurpose,
        guard_text: str,
        sources: tuple[ContextSource, ...],
        on_snapshot: Callable[[ContextSnapshot, int], None],
    ) -> tuple[
        ModelRequirementInterpretation | None,
        int,
        str | None,
        tuple[ContextSnapshot, ...],
    ]:
        if not self._model.available:
            raise ApplicationError(
                ErrorCode.MODEL_UNAVAILABLE,
                "Requirement interpretation model is not configured.",
            )
        last_error = "model output did not satisfy the requirement draft schema"
        snapshots: list[ContextSnapshot] = []
        for attempt in range(1, MAX_MODEL_ATTEMPTS + 1):
            attempt_sources = sources
            if attempt > 1:
                attempt_sources += (
                    ContextSource(
                        source_id="validation_feedback",
                        source_type="schema_validation_feedback",
                        version=f"attempt-{attempt}",
                        trust=ContextTrust.TRUSTED_STRUCTURED,
                        payload={
                            "instruction": (
                                "Return the exact schema and do not invent missing facts."
                            ),
                            "validation_error": last_error,
                        },
                    ),
                )
            snapshot = self._context_builder.build(
                project_id=project_id,
                purpose=purpose,
                prompt=prompt,
                sources=attempt_sources,
            )
            snapshots.append(snapshot)
            on_snapshot(snapshot, attempt)
            if snapshot.status is not ContextBuildStatus.READY:
                code = (
                    ErrorCode.PAYLOAD_TOO_LARGE
                    if snapshot.status is ContextBuildStatus.BUDGET_EXCEEDED
                    else ErrorCode.CONFLICT
                )
                raise ApplicationError(
                    code,
                    "Model context could not be built safely.",
                    context={
                        "context_status": snapshot.status.value,
                        "detail": snapshot.status_detail,
                        "context_hash": snapshot.context_hash,
                    },
                )
            try:
                raw = self._model.generate_structured(
                    prompt=prompt,
                    user_input=snapshot.model_user_input,
                    output_type=ModelRequirementInterpretation,
                )
                parsed = ModelRequirementInterpretation.model_validate(raw)
                return normalize_interpretation(guard_text, parsed), attempt, None, tuple(snapshots)
            except ModelInvocationError as error:
                raise ApplicationError(
                    ErrorCode.MODEL_INVOCATION_FAILED,
                    "Requirement interpretation model call failed.",
                    context={"model_id": self._model.model_id},
                ) from error
            except (ValidationError, ModelOutputError, ValueError, TypeError) as error:
                last_error = str(error)[:1000] or type(error).__name__
        return None, MAX_MODEL_ATTEMPTS, last_error, tuple(snapshots)

    @staticmethod
    def _snapshot_record(
        snapshot: ContextSnapshot,
        *,
        draft_id: str,
        draft_revision: int,
        attempt: int,
        created_at: datetime,
    ) -> ContextSnapshotRecord:
        return ContextSnapshotRecord(
            context_snapshot_id=snapshot.context_snapshot_id,
            context_hash=snapshot.context_hash,
            project_id=snapshot.project_id,
            draft_id=draft_id,
            draft_revision=draft_revision,
            attempt=attempt,
            purpose=snapshot.purpose.value,
            status=snapshot.status.value,
            prompt_name=snapshot.prompt_name,
            prompt_version=snapshot.prompt_version,
            prompt_content_sha256=snapshot.prompt_content_sha256,
            budget_policy_version=snapshot.budget_policy_version,
            estimated_tokens=snapshot.estimated_tokens,
            source_count=snapshot.source_count,
            payload=snapshot.payload,
            created_at=created_at,
        )

    def _persist_snapshot(
        self,
        snapshot: ContextSnapshot,
        *,
        draft_id: str,
        draft_revision: int,
        attempt: int,
    ) -> None:
        record = self._snapshot_record(
            snapshot,
            draft_id=draft_id,
            draft_revision=draft_revision,
            attempt=attempt,
            created_at=datetime.now(UTC),
        )
        with self._uow_factory() as uow:
            existing = uow.context_snapshots.get(snapshot.context_snapshot_id)
            if existing is None:
                uow.context_snapshots.add(record)
                uow.commit()
                return
            if (
                existing.context_hash != snapshot.context_hash
                or existing.payload != snapshot.payload
            ):
                raise ApplicationError(
                    ErrorCode.CONFLICT,
                    "Context snapshot identity conflicts with persisted content.",
                )

    @staticmethod
    def _view(record: RequirementDraftRecord) -> RequirementDraftView:
        payload = record.revision.payload
        interpretation = payload.get("interpretation")
        return RequirementDraftView(
            draft_id=record.draft_id,
            project_id=record.project_id,
            status=record.status,
            revision=record.current_revision,
            source_text=record.revision.source_text,
            source_text_sha256=record.revision.source_text_sha256,
            interpretation=interpretation,
            parse_error=payload.get("parse_error"),
            prompt_name=record.revision.prompt_name,
            prompt_version=record.revision.prompt_version,
            prompt_content_sha256=record.revision.prompt_content_sha256,
            model_id=record.revision.model_id,
            attempt_count=record.revision.attempt_count,
            context_snapshot_ids=record.revision.context_snapshot_ids,
            context_snapshot_hash=payload["context_snapshot_hash"],
            confirmed_requirement_id=record.confirmed_requirement_id,
        )

    def create(self, request: RequirementDraftCreateInput, *, created_by: str):
        request = validate_input(RequirementDraftCreateInput, request)
        source_sha256 = _source_hash(request.natural_language)
        with self._uow_factory() as uow:
            if uow.projects.get(request.project_id) is None:
                raise ApplicationError(ErrorCode.NOT_FOUND, "Project was not found.")
            retry = uow.requirement_drafts.get_revision_by_idempotency_key(request.idempotency_key)
            if retry is not None:
                if retry.draft_id == request.draft_id and retry.source_text_sha256 == source_sha256:
                    current = uow.requirement_drafts.get(retry.draft_id)
                    if current is not None and current.project_id == request.project_id:
                        return self._view(current)
                raise ApplicationError(
                    ErrorCode.CONFLICT,
                    "Draft idempotency key was already used for different content.",
                )
            if uow.requirement_drafts.get(request.draft_id) is not None:
                raise ApplicationError(ErrorCode.CONFLICT, "Requirement draft ID already exists.")

        prompt = self._prompts.get("requirement_parser", "1.0.0")
        interpretation, attempts, parse_error, snapshots = self._generate(
            prompt,
            project_id=request.project_id,
            purpose=ContextPurpose.REQUIREMENT_PARSE,
            guard_text=request.natural_language,
            sources=(
                ContextSource(
                    source_id="request_metadata",
                    source_type="requirement_draft_request",
                    version="v1",
                    trust=ContextTrust.TRUSTED_STRUCTURED,
                    payload={"draft_id": request.draft_id},
                ),
                ContextSource(
                    source_id="natural_language",
                    source_type="user_requirement_text",
                    version=source_sha256,
                    trust=ContextTrust.UNTRUSTED_TEXT,
                    payload=request.natural_language,
                ),
            ),
            on_snapshot=lambda snapshot, attempt: self._persist_snapshot(
                snapshot,
                draft_id=request.draft_id,
                draft_revision=1,
                attempt=attempt,
            ),
        )
        status = (
            _status_for(interpretation)
            if interpretation is not None
            else RequirementDraftStatus.PARSE_FAILED
        )
        now = datetime.now(UTC)
        payload = {
            "interpretation": (
                interpretation.model_dump(mode="json") if interpretation is not None else None
            ),
            "parse_error": parse_error,
            "context_snapshot_hash": snapshots[-1].context_hash,
        }
        revision = RequirementDraftRevisionRecord(
            draft_id=request.draft_id,
            revision=1,
            idempotency_key=request.idempotency_key,
            source_text=request.natural_language,
            source_text_sha256=source_sha256,
            answers=(),
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            prompt_content_sha256=prompt.content_sha256,
            model_id=self._model.model_id,
            attempt_count=attempts,
            context_snapshot_ids=tuple(item.context_snapshot_id for item in snapshots),
            payload=payload,
            created_at=now,
        )
        record = RequirementDraftRecord(
            draft_id=request.draft_id,
            project_id=request.project_id,
            status=status.value,
            current_revision=1,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            confirmed_requirement_id=None,
            revision=revision,
        )
        with self._uow_factory() as uow:
            uow.requirement_drafts.add(record, revision)
            uow.commit()
        return self._view(record)

    def get(self, project_id: str, draft_id: str) -> RequirementDraftView:
        with self._uow_factory() as uow:
            record = uow.requirement_drafts.get(draft_id)
            if record is None or record.project_id != project_id:
                raise ApplicationError(ErrorCode.NOT_FOUND, "Requirement draft was not found.")
            return self._view(record)

    def clarify(self, request: RequirementDraftClarifyInput) -> RequirementDraftView:
        request = validate_input(RequirementDraftClarifyInput, request)
        answers = tuple(item.model_dump(mode="json") for item in request.answers)
        with self._uow_factory() as uow:
            retry = uow.requirement_drafts.get_revision_by_idempotency_key(request.idempotency_key)
            if retry is not None:
                if retry.draft_id == request.draft_id and retry.answers == answers:
                    current = uow.requirement_drafts.get(retry.draft_id)
                    if current is not None and current.project_id == request.project_id:
                        return self._view(current)
                raise ApplicationError(
                    ErrorCode.CONFLICT,
                    "Clarification idempotency key was already used for different content.",
                )
            record = uow.requirement_drafts.get(request.draft_id)
            if record is None or record.project_id != request.project_id:
                raise ApplicationError(ErrorCode.NOT_FOUND, "Requirement draft was not found.")
            if record.status == RequirementDraftStatus.CONFIRMED.value:
                raise ApplicationError(ErrorCode.CONFLICT, "Confirmed drafts cannot be revised.")
            if record.status == RequirementDraftStatus.PARSE_FAILED.value:
                raise ApplicationError(
                    ErrorCode.CONFLICT,
                    "A failed parse has no questions to clarify; create a new draft.",
                )
            current = self._view(record)
            current_questions = {
                item.question_id
                for item in current.interpretation.clarification_questions  # type: ignore[union-attr]
            }
            unknown = sorted(
                item.question_id
                for item in request.answers
                if item.question_id not in current_questions
            )
            if unknown:
                raise ApplicationError(
                    ErrorCode.INPUT_INVALID,
                    "Clarification answers reference unknown question IDs.",
                    context={"question_ids": unknown},
                )

        prompt = self._prompts.get("clarification_generator", "1.0.0")
        previous_interpretation = current.interpretation.model_dump(mode="json")  # type: ignore[union-attr]
        next_revision = record.current_revision + 1
        interpretation, attempts, parse_error, snapshots = self._generate(
            prompt,
            project_id=request.project_id,
            purpose=ContextPurpose.REQUIREMENT_CLARIFICATION,
            guard_text=record.revision.source_text
            + "\n"
            + json.dumps(answers, ensure_ascii=False, sort_keys=True),
            sources=(
                ContextSource(
                    source_id="request_metadata",
                    source_type="clarification_request",
                    version="v1",
                    trust=ContextTrust.TRUSTED_STRUCTURED,
                    payload={"draft_id": record.draft_id, "revision": next_revision},
                ),
                ContextSource(
                    source_id="original_request",
                    source_type="user_requirement_text",
                    version=record.revision.source_text_sha256,
                    trust=ContextTrust.UNTRUSTED_TEXT,
                    payload=record.revision.source_text,
                ),
                ContextSource(
                    source_id="previous_interpretation",
                    source_type="schema_bound_interpretation",
                    version=str(record.current_revision),
                    trust=ContextTrust.TRUSTED_STRUCTURED,
                    payload=previous_interpretation,
                ),
                ContextSource(
                    source_id="clarification_answers",
                    source_type="user_clarification_answers",
                    version=canonical_hash(answers),
                    trust=ContextTrust.UNTRUSTED_TEXT,
                    payload=answers,
                ),
            ),
            on_snapshot=lambda snapshot, attempt: self._persist_snapshot(
                snapshot,
                draft_id=record.draft_id,
                draft_revision=next_revision,
                attempt=attempt,
            ),
        )
        status = (
            _status_for(interpretation)
            if interpretation is not None
            else RequirementDraftStatus.PARSE_FAILED
        )
        now = datetime.now(UTC)
        revision = RequirementDraftRevisionRecord(
            draft_id=record.draft_id,
            revision=next_revision,
            idempotency_key=request.idempotency_key,
            source_text=record.revision.source_text,
            source_text_sha256=record.revision.source_text_sha256,
            answers=answers,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            prompt_content_sha256=prompt.content_sha256,
            model_id=self._model.model_id,
            attempt_count=attempts,
            context_snapshot_ids=tuple(item.context_snapshot_id for item in snapshots),
            payload={
                "interpretation": (
                    interpretation.model_dump(mode="json") if interpretation is not None else None
                ),
                "parse_error": parse_error,
                "context_snapshot_hash": snapshots[-1].context_hash,
            },
            created_at=now,
        )
        with self._uow_factory() as uow:
            latest = uow.requirement_drafts.get(record.draft_id)
            if latest is None or latest.current_revision != record.current_revision:
                raise ApplicationError(
                    ErrorCode.CONFLICT,
                    "Requirement draft changed while clarification context was being evaluated.",
                    context={"context_status": ContextBuildStatus.STALE_INPUT.value},
                )
            uow.requirement_drafts.add_revision(
                record.draft_id,
                status=status.value,
                updated_at=now,
                revision=revision,
            )
            uow.commit()
        with self._uow_factory() as uow:
            updated = uow.requirement_drafts.get(record.draft_id)
        if updated is None:
            raise RuntimeError("requirement draft disappeared after revision")
        return self._view(updated)

    @staticmethod
    def _matches_proposal(submitted: object, proposed: object) -> bool:
        if proposed is None:
            return True
        if isinstance(submitted, Decimal) and isinstance(proposed, (int, float)):
            return submitted == Decimal(str(proposed))
        submitted_value = getattr(submitted, "value", submitted)
        proposed_value = getattr(proposed, "value", proposed)
        return submitted_value == proposed_value

    def confirm(
        self, request: RequirementDraftConfirmInput, *, confirmed_by: str
    ) -> RequirementRecord:
        request = validate_input(RequirementDraftConfirmInput, request)
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            draft = uow.requirement_drafts.get(request.draft_id)
            if draft is None or draft.project_id != request.project_id:
                raise ApplicationError(ErrorCode.NOT_FOUND, "Requirement draft was not found.")
            if draft.status == RequirementDraftStatus.CONFIRMED.value:
                existing = uow.requirements.get(request.requirement_id)
                if (
                    existing is not None
                    and draft.confirmed_requirement_id == request.requirement_id
                    and existing.idempotency_key == request.idempotency_key
                    and existing.slot_requirement_version_id == request.slot_requirement_version_id
                    and self._confirmation_payload_matches(existing, request)
                ):
                    return existing
                raise ApplicationError(
                    ErrorCode.CONFLICT, "Requirement draft is already confirmed."
                )
            if draft.status != RequirementDraftStatus.READY_FOR_CONFIRMATION.value:
                raise ApplicationError(
                    ErrorCode.CONFLICT,
                    "Only a ready_for_confirmation draft can become a formal requirement.",
                    context={"draft_status": draft.status},
                )
            interpretation = ModelRequirementInterpretation.model_validate(
                draft.revision.payload["interpretation"]
            )
            proposals = {item.slot_id: item for item in interpretation.slots}
            submitted_ids = {item.slot_id for item in request.slots}
            if None in proposals or set(proposals) != submitted_ids:
                raise ApplicationError(
                    ErrorCode.CONFLICT,
                    "Confirmed slot identities must match the ready draft.",
                )
            proposed_preferences = {
                item.preference_type: item for item in interpretation.soft_preferences
            }
            submitted_preferences = {
                item.preference_type: item for item in request.soft_preferences
            }
            if set(proposed_preferences) != set(submitted_preferences):
                raise ApplicationError(
                    ErrorCode.CONFLICT,
                    "Confirmed soft preference identities must match the ready draft.",
                )
            preference_mismatches = []
            for preference_type, submitted in submitted_preferences.items():
                proposed = proposed_preferences[preference_type]
                if (
                    not self._matches_proposal(submitted.weight, proposed.requested_weight)
                    or submitted.scope != proposed.scope
                    or submitted.rationale != proposed.rationale
                ):
                    preference_mismatches.append(preference_type)
            if preference_mismatches:
                raise ApplicationError(
                    ErrorCode.CONFLICT,
                    "Confirmed soft preferences differ from the ready draft.",
                    context={"preference_types": sorted(preference_mismatches)},
                )
            comparable_fields = tuple(_REQUIRED_SLOT_FIELDS) + (
                ("minimum_strength_grade", "text"),
                ("capacity_requirement_ref", "text"),
                ("defect_restriction_profile_id", "text"),
                ("max_moisture_content_pct", "decimal"),
                ("treatment_restriction_profile_id", "text"),
                ("visible_surface_scope", "choice"),
                ("aesthetic_requirement_text", "text"),
                ("visual_reference_ref", "text"),
            )
            mismatches = []
            for submitted in request.slots:
                proposed = proposals[submitted.slot_id]
                for field_name, _ in comparable_fields:
                    if not self._matches_proposal(
                        getattr(submitted, field_name), getattr(proposed, field_name)
                    ):
                        mismatches.append(f"{submitted.slot_id}.{field_name}")
            if mismatches:
                raise ApplicationError(
                    ErrorCode.CONFLICT,
                    "Confirmation differs from extracted draft facts; clarify the draft first.",
                    context={"fields": sorted(mismatches)},
                )

            slots = tuple(
                SlotRequirement(
                    project_id=request.project_id,
                    slot_requirement_version_id=request.slot_requirement_version_id,
                    confirmed_by=confirmed_by,
                    confirmed_at=now,
                    source_row_sha256=canonical_hash(
                        {
                            "draft_id": draft.draft_id,
                            "draft_revision": draft.current_revision,
                            "slot": item.model_dump(mode="json"),
                        }
                    ),
                    **item.model_dump(),
                )
                for item in request.slots
            )
            ordered_slots = sorted(slots, key=lambda item: item.slot_id)
            slot_payloads = tuple(slot.model_dump(mode="json") for slot in ordered_slots)
            preference_payloads = tuple(
                item.model_dump(mode="json")
                for item in sorted(request.soft_preferences, key=lambda item: item.preference_type)
            )
            content_hash = canonical_hash(
                {"slots": slot_payloads, "soft_preferences": preference_payloads}
            )
            existing_key = uow.requirements.get_by_idempotency_key(request.idempotency_key)
            if existing_key is not None:
                raise ApplicationError(
                    ErrorCode.CONFLICT,
                    "Requirement idempotency key was already used.",
                )
            if uow.requirements.get(request.requirement_id) is not None:
                raise ApplicationError(ErrorCode.CONFLICT, "Requirement ID already exists.")
            result = RequirementRecord(
                requirement_id=request.requirement_id,
                project_id=request.project_id,
                idempotency_key=request.idempotency_key,
                slot_requirement_version_id=request.slot_requirement_version_id,
                content_hash=content_hash,
                slots=slot_payloads,
                confirmed_by=confirmed_by,
                confirmed_at=now,
                soft_preferences=preference_payloads,
            )
            uow.requirements.add(result)
            uow.requirement_drafts.mark_confirmed(
                draft.draft_id, request.requirement_id, updated_at=now
            )
            uow.commit()
            return result

    @staticmethod
    def _confirmation_payload_matches(
        existing: RequirementRecord, request: RequirementDraftConfirmInput
    ) -> bool:
        server_fields = {
            "project_id",
            "slot_requirement_version_id",
            "confirmed_by",
            "confirmed_at",
            "source_row_sha256",
        }
        existing_slots = {
            item["slot_id"]: {key: value for key, value in item.items() if key not in server_fields}
            for item in existing.slots
        }
        requested_slots = {item.slot_id: item.model_dump(mode="json") for item in request.slots}
        requested_preferences = tuple(
            item.model_dump(mode="json")
            for item in sorted(request.soft_preferences, key=lambda item: item.preference_type)
        )
        return (
            existing_slots == requested_slots and existing.soft_preferences == requested_preferences
        )
