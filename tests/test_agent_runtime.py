from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from material_allocation_copilot.adapters.openai_agent import OpenAIAgentModel
from material_allocation_copilot.application import (
    ConfirmedRequirementInput,
    InventoryImportInput,
    InventoryService,
    ProjectService,
    RequirementService,
    TaskService,
)
from material_allocation_copilot.application.access import Principal, Role
from material_allocation_copilot.application.agent_runtime import (
    AgentBudget,
    AgentFunctionCall,
    AgentModelRequest,
    AgentModelTimeout,
    AgentModelTurn,
    AgentRunInput,
    AgentUsage,
    ApprovalResolutionInput,
    BoundedAgentOrchestrator,
)
from material_allocation_copilot.application.contracts import AgentRunStatus
from material_allocation_copilot.domain import (
    AssessmentMethod,
    AvailabilityStatus,
    DefectDataStatus,
    MaterialItem,
    OrientationRequirement,
    SlotRequirement,
    StructuralUseStatus,
)
from material_allocation_copilot.persistence import (
    SqlAlchemyUnitOfWork,
    create_db_engine,
    create_session_factory,
)
from material_allocation_copilot.persistence.models import Base

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


class ScriptedModel:
    available = True
    model_id = "test-agent"

    def __init__(self, turns: list[AgentModelTurn]) -> None:
        self.turns = turns
        self.requests: list[AgentModelRequest] = []

    def respond(self, request: AgentModelRequest) -> AgentModelTurn:
        self.requests.append(request)
        return self.turns.pop(0)


def call(response_id: str, call_id: str, name: str, arguments: dict) -> AgentModelTurn:
    return AgentModelTurn(
        response_id=response_id,
        function_calls=(
            AgentFunctionCall(
                call_id=call_id,
                name=name,
                arguments_json=json.dumps(arguments),
            ),
        ),
        usage=AgentUsage(input_tokens=100, output_tokens=20, cost_microusd=200),
    )


@pytest.fixture
def agent_context(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'agent.db').as_posix()}")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    material = MaterialItem.model_validate(
        {
            "material_id": "MAT-001",
            "inventory_version_id": "INV-001",
            "source_system": "test_scanner",
            "source_record_id": "SCAN-001",
            "source_file_sha256": HASH_A,
            "source_row_sha256": HASH_B,
            "measured_at": datetime(2026, 10, 7, 9, 0, tzinfo=UTC),
            "length_mm": "4000",
            "section_width_mm": "100",
            "section_height_mm": "200",
            "structural_use_status": StructuralUseStatus.ELIGIBLE,
            "strength_grade": "C24",
            "assessment_method": AssessmentMethod.ENGINEERING_ASSESSMENT,
            "assessor_id": "ENG-01",
            "assessment_date": date(2026, 10, 7),
            "assessment_evidence_ref": "TEST-001",
            "defect_data_status": DefectDataStatus.COMPLETE,
            "defect_evidence_ref": "SCAN-001",
            "defects": [],
            "availability_status": AvailabilityStatus.AVAILABLE,
        }
    )
    slot = SlotRequirement.model_validate(
        {
            "project_id": "PRJ-001",
            "slot_requirement_version_id": "SLOTS-v1",
            "slot_id": "SLOT-B01",
            "slot_role": "beam",
            "clear_span_mm": "3000",
            "end_allowance_mm": "100",
            "min_section_width_mm": "90",
            "min_section_height_mm": "190",
            "allow_section_rotation": False,
            "minimum_strength_grade": "C24",
            "orientation_requirement": OrientationRequirement.FIXED,
            "rule_set_version": "rules-0.1.1",
            "requirement_source_ref": "DWG-S201#B01",
            "confirmed_by": "ENG-01",
            "confirmed_at": datetime(2026, 10, 7, 9, 30, tzinfo=UTC),
            "source_row_sha256": HASH_C,
        }
    )
    ProjectService(uow_factory).create_project(project_id="PRJ-001", name="Pilot frame")
    InventoryService(uow_factory).import_inventory(
        InventoryImportInput(
            project_id="PRJ-001",
            inventory_version_id="INV-001",
            idempotency_key="import-001",
            source_system="test_scanner",
            source_path="scanner/batch-001.csv",
            source_sha256=HASH_A,
            imported_at=datetime(2026, 10, 7, 9, 5, tzinfo=UTC),
            materials=(material,),
        )
    )
    RequirementService(uow_factory).save_confirmed(
        ConfirmedRequirementInput(
            requirement_id="REQ-001",
            project_id="PRJ-001",
            idempotency_key="requirement-001",
            slots=(slot,),
        )
    )
    TaskService(uow_factory).create_task(
        allocation_task_id="TASK-001",
        project_id="PRJ-001",
        inventory_version_id="INV-001",
        requirement_id="REQ-001",
        idempotency_key="task-001",
    )
    planner = Principal(
        user_id="PLANNER-01",
        roles=frozenset({Role.PLANNER}),
        project_ids=frozenset({"PRJ-001"}),
    )
    reviewer = Principal(
        user_id="REVIEWER-01",
        roles=frozenset({Role.REVIEWER}),
        project_ids=frozenset({"PRJ-001"}),
    )
    yield uow_factory, planner, reviewer
    engine.dispose()


def request(run_id: str = "RUN-001", budget: AgentBudget | None = None) -> AgentRunInput:
    return AgentRunInput(
        run_id=run_id,
        allocation_task_id="TASK-001",
        goal="Find and explain a feasible preview.",
        budget=budget or AgentBudget(),
    )


def test_agent_uses_read_only_tool_then_finishes_with_audited_trace(agent_context) -> None:
    uow_factory, planner, _ = agent_context
    model = ScriptedModel(
        [
            call(
                "RESP-1",
                "CALL-1",
                "solve_allocation",
                {"allocation_task_id": "TASK-001"},
            ),
            call(
                "RESP-2",
                "CALL-2",
                "finish_agent_run",
                {
                    "status": "success",
                    "summary": "A deterministic feasible preview is available.",
                    "evidence_tool_call_ids": ["CALL-1"],
                },
            ),
        ]
    )
    result = BoundedAgentOrchestrator(uow_factory, model).run(planner, "PRJ-001", request())

    assert result.run.status == AgentRunStatus.SUCCESS
    assert result.run.usage_payload == {
        "model_turns": 2,
        "tool_calls": 1,
        "input_tokens": 200,
        "output_tokens": 40,
        "total_tokens": 240,
        "cost_microusd": 400,
    }
    assert [item.event_type for item in result.trace] == [
        "run_started",
        "model_response",
        "tool_result",
        "model_response",
        "run_finished",
    ]
    assert model.requests[1].previous_response_id == "RESP-1"
    assert model.requests[1].input[0]["type"] == "function_call_output"
    assert all(tool["strict"] is True for tool in model.requests[0].tools)
    assert all(
        set(tool["parameters"]["required"]) == set(tool["parameters"]["properties"])
        for tool in model.requests[0].tools
    )
    with uow_factory() as uow:
        assert len(uow.tool_calls.list_by_run("RUN-001")) == 1
        assert uow.plans.get_by_task_version("TASK-001", 1) is None


def test_agent_pauses_for_human_approval_without_executing_write(agent_context) -> None:
    uow_factory, planner, reviewer = agent_context
    model = ScriptedModel(
        [
            call(
                "RESP-1",
                "CALL-1",
                "request_human_approval",
                {
                    "action": "approve_plan",
                    "resource_id": "PLAN-001",
                    "reason": "A reviewer must decide before approval.",
                },
            )
        ]
    )
    service = BoundedAgentOrchestrator(uow_factory, model)
    pending = service.run(planner, "PRJ-001", request())
    assert pending.run.status == AgentRunStatus.AWAITING_APPROVAL
    assert pending.approval is not None and pending.approval.status.value == "pending"
    assert pending.run.final_payload["execution_performed"] is False

    resolved = service.resolve_approval(
        reviewer,
        "PRJ-001",
        "RUN-001",
        ApprovalResolutionInput(decision="approved", note="Reviewed."),
    )
    assert resolved.run.status == AgentRunStatus.APPROVAL_GRANTED
    assert resolved.approval is not None and resolved.approval.decided_by == "REVIEWER-01"
    assert resolved.run.final_payload["execution_performed"] is False


def test_agent_stops_duplicate_calls_and_token_budget(agent_context) -> None:
    uow_factory, planner, _ = agent_context
    duplicate = call(
        "RESP-1",
        "CALL-1",
        "solve_allocation",
        {"allocation_task_id": "TASK-001"},
    )
    model = ScriptedModel([duplicate, duplicate.model_copy(update={"response_id": "RESP-2"})])
    result = BoundedAgentOrchestrator(uow_factory, model).run(planner, "PRJ-001", request())
    assert result.run.status == AgentRunStatus.TOOL_ERROR
    assert result.run.error_code == "duplicate_tool_call"

    costly = ScriptedModel(
        [
            AgentModelTurn(
                response_id="RESP-COST",
                function_calls=duplicate.function_calls,
                usage=AgentUsage(input_tokens=300, output_tokens=300, cost_microusd=1),
            )
        ]
    )
    budgeted = BoundedAgentOrchestrator(uow_factory, costly).run(
        planner,
        "PRJ-001",
        request("RUN-002", AgentBudget(max_total_tokens=256)),
    )
    assert budgeted.run.status == AgentRunStatus.BUDGET_EXHAUSTED
    assert budgeted.run.error_code == "token_budget_exhausted"


def test_openai_adapter_enforces_serial_calls_deadline_and_cost_accounting() -> None:
    class Responses:
        def __init__(self) -> None:
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            return {
                "id": "resp_123",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_123",
                        "name": "finish_agent_run",
                        "arguments": '{"status":"success","summary":"done"}',
                    }
                ],
                "output_text": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

    class Client:
        def __init__(self) -> None:
            self.responses = Responses()

    client = Client()
    adapter = OpenAIAgentModel(
        client,
        "test-model",
        input_cost_per_million_usd=Decimal("2.5"),
        output_cost_per_million_usd=Decimal("10"),
    )
    turn = adapter.respond(
        AgentModelRequest(
            instructions="bounded",
            input="goal",
            tools=(),
            timeout_seconds=3.5,
            max_output_tokens=256,
        )
    )
    assert client.responses.kwargs["parallel_tool_calls"] is False
    assert client.responses.kwargs["timeout"] == 3.5
    assert client.responses.kwargs["max_output_tokens"] == 256
    assert turn.function_calls[0].name == "finish_agent_run"
    assert turn.usage.cost_microusd == 75


def test_agent_has_finite_no_solution_timeout_and_protocol_error_exits(agent_context) -> None:
    uow_factory, planner, _ = agent_context
    no_solution = ScriptedModel(
        [
            call(
                "RESP-NONE",
                "CALL-NONE",
                "finish_agent_run",
                {"status": "no_solution", "summary": "No valid complete assignment."},
            )
        ]
    )
    no_solution_result = BoundedAgentOrchestrator(uow_factory, no_solution).run(
        planner, "PRJ-001", request("RUN-NONE")
    )
    assert no_solution_result.run.status == AgentRunStatus.NO_SOLUTION

    invalid = ScriptedModel([AgentModelTurn(response_id="RESP-INVALID")])
    invalid_result = BoundedAgentOrchestrator(uow_factory, invalid).run(
        planner, "PRJ-001", request("RUN-INVALID")
    )
    assert invalid_result.run.status == AgentRunStatus.TOOL_ERROR
    assert invalid_result.run.error_code == "model_protocol_error"

    class TimeoutModel:
        available = True
        model_id = "timeout-model"

        def respond(self, request: AgentModelRequest) -> AgentModelTurn:
            assert request.timeout_seconds <= 30
            raise AgentModelTimeout

    timeout_result = BoundedAgentOrchestrator(uow_factory, TimeoutModel()).run(
        planner, "PRJ-001", request("RUN-TIMEOUT")
    )
    assert timeout_result.run.status == AgentRunStatus.BUDGET_EXHAUSTED
    assert timeout_result.run.error_code == "agent_timeout"
