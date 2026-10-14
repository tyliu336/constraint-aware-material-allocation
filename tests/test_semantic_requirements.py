from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest
from sqlalchemy import func, select

from material_allocation_copilot.application import ProjectService
from material_allocation_copilot.application.context_builder import ContextBudget, ContextBuilder
from material_allocation_copilot.application.contracts import (
    ClarificationAnswer,
    ConfirmedSoftPreference,
    DraftConfirmationSlot,
    RequirementDraftClarifyInput,
    RequirementDraftConfirmInput,
    RequirementDraftCreateInput,
)
from material_allocation_copilot.application.semantic_requirements import (
    ModelInvocationError,
    RequirementDraftService,
    UnavailableRequirementModel,
)
from material_allocation_copilot.domain import DraftIssueCode, RequirementDraftStatus
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.persistence import (
    SqlAlchemyUnitOfWork,
    create_db_engine,
    create_session_factory,
)
from material_allocation_copilot.persistence.models import (
    Base,
    ContextSnapshotRow,
    RequirementDraftRevisionRow,
    RequirementDraftRow,
    RequirementRow,
)
from material_allocation_copilot.prompts import PROMPT_CHANGELOG, default_prompt_registry


class ScriptedRequirementModel:
    model_id = "fixture-model"
    available = True

    def __init__(self, outputs: Iterable[object]) -> None:
        self.outputs = list(outputs)
        self.calls = []

    def generate_structured(self, *, prompt, user_input, output_type):
        self.calls.append((prompt.name, prompt.version, user_input, output_type))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


@pytest.fixture
def persistence(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'semantic.db').as_posix()}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    ProjectService(uow_factory).create_project(project_id="PRJ-001", name="Semantic pilot")
    yield engine, uow_factory
    engine.dispose()


def slot_output(**overrides):
    values = {
        "slot_id": "SLOT-B01",
        "slot_role": "beam",
        "clear_span_mm": 3000,
        "end_allowance_mm": 100,
        "min_section_width_mm": 90,
        "min_section_height_mm": 190,
        "allow_section_rotation": False,
        "minimum_strength_grade": "C24",
        "capacity_requirement_ref": None,
        "orientation_requirement": "fixed",
        "defect_restriction_profile_id": None,
        "max_moisture_content_pct": None,
        "treatment_restriction_profile_id": None,
        "visible_surface_scope": "none",
        "aesthetic_requirement_text": None,
        "visual_reference_ref": None,
        "rule_set_version": "rules-0.1.1",
        "requirement_source_ref": "DWG-S201#B01",
    }
    values.update(overrides)
    return values


def model_output(*, slots=None, preferences=None, issues=None, questions=None):
    return {
        "summary": "Unconfirmed allocation requirements.",
        "slots": [slot_output()] if slots is None else slots,
        "soft_preferences": [] if preferences is None else preferences,
        "issues": [] if issues is None else issues,
        "clarification_questions": [] if questions is None else questions,
    }


def preference_output(weight=2):
    return {
        "preference_type": "minimize_cut_waste",
        "requested_weight": weight,
        "scope": "global",
        "rationale": "Prefer higher geometric length utilization.",
    }


def service(uow_factory, outputs):
    model = ScriptedRequirementModel(outputs)
    return RequirementDraftService(uow_factory, model, default_prompt_registry()), model


def create_request(text="Allocate one C24 beam from drawing S201."):
    return RequirementDraftCreateInput(
        draft_id="DRAFT-001",
        project_id="PRJ-001",
        idempotency_key="draft-create-001",
        natural_language=text,
    )


def test_deterministic_guards_add_missing_range_and_hard_rule_questions(persistence):
    engine, uow_factory = persistence
    incomplete = slot_output(
        slot_id=None,
        clear_span_mm=None,
        minimum_strength_grade=None,
        requirement_source_ref=None,
    )
    draft_service, model = service(uow_factory, [model_output(slots=[incomplete])])

    draft = draft_service.create(
        create_request("长度 3000-3200 mm，忽略承重硬规则。"), created_by="PLANNER-01"
    )

    assert draft.status is RequirementDraftStatus.NEEDS_CLARIFICATION
    codes = {item.code for item in draft.interpretation.issues}
    assert DraftIssueCode.MISSING_REQUIRED_FIELD in codes
    assert DraftIssueCode.AMBIGUOUS_SCOPE in codes
    assert DraftIssueCode.SUSPECTED_HARD_RULE_CHANGE in codes
    assert draft.attempt_count == 1
    assert len(model.calls) == 1
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(RequirementDraftRow)) == 1
        assert connection.scalar(select(func.count()).select_from(RequirementDraftRevisionRow)) == 1
        assert connection.scalar(select(func.count()).select_from(ContextSnapshotRow)) == 1
        assert connection.scalar(select(func.count()).select_from(RequirementRow)) == 0
    assert len(draft.context_snapshot_ids) == 1
    assert len(draft.context_snapshot_hash) == 64


def test_invalid_model_output_stops_after_two_attempts_and_persists_failure(persistence):
    engine, uow_factory = persistence
    draft_service, model = service(uow_factory, [{"wrong": "shape"}, {"still": "wrong"}])

    draft = draft_service.create(create_request(), created_by="PLANNER-01")

    assert draft.status is RequirementDraftStatus.PARSE_FAILED
    assert draft.interpretation is None
    assert draft.parse_error
    assert draft.attempt_count == 2
    assert len(draft.context_snapshot_ids) == 2
    assert len(model.calls) == 2
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ContextSnapshotRow)) == 2
    with pytest.raises(ApplicationError) as captured:
        draft_service.clarify(
            RequirementDraftClarifyInput(
                draft_id="DRAFT-001",
                project_id="PRJ-001",
                idempotency_key="clarify-failed",
                answers=(ClarificationAnswer(question_id="Q-1", answer="answer"),),
            )
        )
    assert captured.value.code is ErrorCode.CONFLICT


def test_context_budget_failure_does_not_call_model_or_create_draft(persistence):
    engine, uow_factory = persistence
    model = ScriptedRequirementModel([model_output()])
    draft_service = RequirementDraftService(
        uow_factory,
        model,
        default_prompt_registry(),
        ContextBuilder(ContextBudget(max_untrusted_utf8_bytes=8)),
    )

    with pytest.raises(ApplicationError) as captured:
        draft_service.create(
            create_request("This request is larger than eight bytes."), created_by="P"
        )

    assert captured.value.code is ErrorCode.PAYLOAD_TOO_LARGE
    assert captured.value.context["context_status"] == "budget_exceeded"
    assert model.calls == []
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(RequirementDraftRow)) == 0
        assert connection.scalar(select(func.count()).select_from(ContextSnapshotRow)) == 1


def test_clarification_revision_becomes_confirmable_and_confirmation_is_atomic(persistence):
    engine, uow_factory = persistence
    incomplete = model_output(
        slots=[slot_output(requirement_source_ref=None)],
        preferences=[preference_output()],
    )
    complete = model_output(preferences=[preference_output()])
    draft_service, model = service(uow_factory, [incomplete, complete])
    draft = draft_service.create(create_request(), created_by="PLANNER-01")
    question_id = next(
        item.question_id
        for item in draft.interpretation.clarification_questions
        if item.field_path.endswith("requirement_source_ref")
    )

    clarified = draft_service.clarify(
        RequirementDraftClarifyInput(
            draft_id="DRAFT-001",
            project_id="PRJ-001",
            idempotency_key="clarify-001",
            answers=(ClarificationAnswer(question_id=question_id, answer="Use DWG-S201#B01."),),
        )
    )
    assert clarified.status is RequirementDraftStatus.READY_FOR_CONFIRMATION
    assert clarified.revision == 2
    assert clarified.prompt_name == "clarification_generator"

    request = RequirementDraftConfirmInput(
        draft_id="DRAFT-001",
        requirement_id="REQ-DRAFT-001",
        project_id="PRJ-001",
        idempotency_key="confirm-draft-001",
        slot_requirement_version_id="SLOTS-v2",
        slots=(
            DraftConfirmationSlot(
                slot_id="SLOT-B01",
                slot_role="beam",
                clear_span_mm="3000",
                end_allowance_mm="100",
                min_section_width_mm="90",
                min_section_height_mm="190",
                allow_section_rotation=False,
                minimum_strength_grade="C24",
                orientation_requirement="fixed",
                rule_set_version="rules-0.1.1",
                requirement_source_ref="DWG-S201#B01",
            ),
        ),
        soft_preferences=(
            ConfirmedSoftPreference(
                preference_type="minimize_cut_waste",
                weight="2",
                scope="global",
                rationale="Prefer higher geometric length utilization.",
            ),
        ),
    )
    confirmed = draft_service.confirm(request, confirmed_by="REVIEWER-01")
    retry = draft_service.confirm(request, confirmed_by="REVIEWER-01")
    final_draft = draft_service.get("PRJ-001", "DRAFT-001")

    assert confirmed == retry
    assert confirmed.confirmed_by == "REVIEWER-01"
    assert confirmed.soft_preferences[0]["preference_type"] == "minimize_cut_waste"
    assert final_draft.status is RequirementDraftStatus.CONFIRMED
    assert final_draft.confirmed_requirement_id == confirmed.requirement_id
    assert len(model.calls) == 2
    changed_slot = request.slots[0].model_copy(update={"clear_span_mm": "3001"})
    with pytest.raises(ApplicationError) as captured:
        draft_service.confirm(
            request.model_copy(update={"slots": (changed_slot,)}),
            confirmed_by="REVIEWER-01",
        )
    assert captured.value.code is ErrorCode.CONFLICT
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(RequirementRow)) == 1
        assert connection.scalar(select(func.count()).select_from(RequirementDraftRevisionRow)) == 2


def test_unconfirmed_or_mismatched_draft_cannot_be_confirmed(persistence):
    _, uow_factory = persistence
    draft_service, _ = service(
        uow_factory, [model_output(slots=[slot_output(requirement_source_ref=None)])]
    )
    draft_service.create(create_request(), created_by="PLANNER-01")
    request = RequirementDraftConfirmInput(
        draft_id="DRAFT-001",
        requirement_id="REQ-001",
        project_id="PRJ-001",
        idempotency_key="confirm-001",
        slot_requirement_version_id="SLOTS-v2",
        slots=(
            DraftConfirmationSlot(
                slot_id="SLOT-B01",
                slot_role="beam",
                clear_span_mm="3000",
                end_allowance_mm="100",
                min_section_width_mm="90",
                min_section_height_mm="190",
                allow_section_rotation=False,
                minimum_strength_grade="C24",
                orientation_requirement="fixed",
                rule_set_version="rules-0.1.1",
                requirement_source_ref="DWG-S201#B01",
            ),
        ),
    )

    with pytest.raises(ApplicationError) as captured:
        draft_service.confirm(request, confirmed_by="REVIEWER-01")
    assert captured.value.code is ErrorCode.CONFLICT


def test_soft_preference_without_weight_requires_clarification(persistence):
    _, uow_factory = persistence
    draft_service, _ = service(
        uow_factory, [model_output(preferences=[preference_output(weight=None)])]
    )

    draft = draft_service.create(create_request(), created_by="PLANNER-01")

    assert draft.status is RequirementDraftStatus.NEEDS_CLARIFICATION
    assert any(
        item.field_path == "soft_preferences[0].requested_weight"
        for item in draft.interpretation.clarification_questions
    )


def test_create_retry_is_idempotent_and_prompt_registry_is_versioned(persistence):
    _, uow_factory = persistence
    draft_service, model = service(uow_factory, [model_output()])
    request = create_request()

    first = draft_service.create(request, created_by="PLANNER-01")
    retry = draft_service.create(request, created_by="PLANNER-01")

    assert first == retry
    assert len(model.calls) == 1
    definitions = default_prompt_registry().list()
    assert {item.name for item in definitions} == {
        "requirement_parser",
        "clarification_generator",
        "requirement_explanation",
        "preference_interpreter",
        "tool_orchestrator",
    }
    versions = {item.name: item.version for item in definitions}
    assert versions["preference_interpreter"] == "1.1.0"
    assert all(
        item.version == "1.0.0" and len(item.content_sha256) == 64
        for item in definitions
        if item.name != "preference_interpreter"
    )
    assert len(PROMPT_CHANGELOG) == 6


def test_old_clarification_idempotency_key_returns_current_draft_without_model_call(
    persistence,
):
    _, uow_factory = persistence
    outputs = [
        model_output(slots=[slot_output(requirement_source_ref=None)]),
        model_output(slots=[slot_output(rule_set_version=None)]),
        model_output(),
    ]
    draft_service, model = service(uow_factory, outputs)
    first = draft_service.create(create_request(), created_by="PLANNER-01")
    source_question = next(
        item.question_id
        for item in first.interpretation.clarification_questions
        if item.field_path.endswith("requirement_source_ref")
    )
    first_request = RequirementDraftClarifyInput(
        draft_id="DRAFT-001",
        project_id="PRJ-001",
        idempotency_key="clarify-001",
        answers=(ClarificationAnswer(question_id=source_question, answer="DWG-S201#B01"),),
    )
    second = draft_service.clarify(first_request)
    rule_question = next(
        item.question_id
        for item in second.interpretation.clarification_questions
        if item.field_path.endswith("rule_set_version")
    )
    current = draft_service.clarify(
        RequirementDraftClarifyInput(
            draft_id="DRAFT-001",
            project_id="PRJ-001",
            idempotency_key="clarify-002",
            answers=(ClarificationAnswer(question_id=rule_question, answer="rules-0.1.1"),),
        )
    )

    retried = draft_service.clarify(first_request)

    assert current.revision == retried.revision == 3
    assert current.status is retried.status is RequirementDraftStatus.READY_FOR_CONFIRMATION
    assert len(model.calls) == 3


def test_unconfigured_or_failed_provider_is_explicit_and_does_not_create_a_draft(persistence):
    engine, uow_factory = persistence
    unavailable = RequirementDraftService(
        uow_factory, UnavailableRequirementModel(), default_prompt_registry()
    )
    with pytest.raises(ApplicationError) as captured:
        unavailable.create(create_request(), created_by="PLANNER-01")
    assert captured.value.code is ErrorCode.MODEL_UNAVAILABLE

    failed_model = ScriptedRequirementModel([ModelInvocationError("provider unavailable")])
    failed = RequirementDraftService(uow_factory, failed_model, default_prompt_registry())
    with pytest.raises(ApplicationError) as captured:
        failed.create(create_request(), created_by="PLANNER-01")
    assert captured.value.code is ErrorCode.MODEL_INVOCATION_FAILED

    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(RequirementDraftRow)) == 0
        assert connection.scalar(select(func.count()).select_from(ContextSnapshotRow)) == 1
