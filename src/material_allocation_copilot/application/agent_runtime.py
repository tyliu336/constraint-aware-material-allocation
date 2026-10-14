"""Bounded, auditable single-agent orchestration over the read-only tool allowlist."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from material_allocation_copilot.application.access import Principal, Role
from material_allocation_copilot.application.contracts import (
    AgentActionType,
    AgentApprovalStatus,
    AgentRunStatus,
)
from material_allocation_copilot.application.hashing import canonical_hash
from material_allocation_copilot.application.queries import QueryService
from material_allocation_copilot.application.records import (
    AgentApprovalRequestRecord,
    AgentRunRecord,
    AgentTraceRecord,
)
from material_allocation_copilot.application.services import UnitOfWorkFactory
from material_allocation_copilot.application.tooling import ToolExecutor, strict_function_parameters
from material_allocation_copilot.domain.types import Identifier
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.prompts import PromptRegistry, default_prompt_registry


class AgentModelTimeout(TimeoutError):
    """The model did not answer inside the caller-owned deadline."""


class AgentModelInvocationError(RuntimeError):
    """The model transport failed without yielding a valid response."""


class AgentBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_model_turns: int = Field(default=5, ge=1, le=8)
    max_tool_calls: int = Field(default=6, ge=1, le=12)
    max_elapsed_ms: int = Field(default=30_000, ge=1_000, le=120_000)
    max_total_tokens: int = Field(default=12_000, ge=256, le=100_000)
    max_cost_microusd: int = Field(default=250_000, ge=0, le=10_000_000)
    max_output_tokens_per_turn: int = Field(default=1_500, ge=64, le=8_192)


class AgentRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: Identifier
    allocation_task_id: Identifier
    goal: str = Field(min_length=1, max_length=4_000)
    budget: AgentBudget = Field(default_factory=AgentBudget)


class AgentFunctionCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: Identifier
    name: str = Field(min_length=1, max_length=128)
    arguments_json: str = Field(max_length=64_000)


class AgentUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_microusd: int = Field(default=0, ge=0)


class AgentModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instructions: str
    input: str | tuple[dict[str, Any], ...]
    previous_response_id: str | None = None
    tools: tuple[dict[str, Any], ...]
    timeout_seconds: float = Field(gt=0)
    max_output_tokens: int = Field(ge=1)


class AgentModelTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    response_id: str = Field(min_length=1, max_length=200)
    function_calls: tuple[AgentFunctionCall, ...] = ()
    output_text: str | None = Field(default=None, max_length=8_000)
    usage: AgentUsage = Field(default_factory=AgentUsage)


class AgentModel(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def model_id(self) -> str: ...

    def respond(self, request: AgentModelRequest) -> AgentModelTurn: ...


class UnavailableAgentModel:
    available = False
    model_id = "unavailable"

    def respond(self, request: AgentModelRequest) -> AgentModelTurn:
        del request
        raise AgentModelInvocationError("Agent model is not configured.")


class FinishAgentRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["success", "needs_clarification", "no_solution"]
    summary: str = Field(min_length=1, max_length=2_000)
    evidence_tool_call_ids: tuple[Identifier, ...] = Field(default=(), max_length=20)


class RequestHumanApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: AgentActionType
    resource_id: Identifier
    reason: str = Field(min_length=1, max_length=1_000)


class ApprovalResolutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=2_000)


@dataclass(frozen=True, slots=True)
class AgentRunView:
    run: AgentRunRecord
    trace: tuple[AgentTraceRecord, ...]
    approval: AgentApprovalRequestRecord | None


def _control_tool(name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": strict_function_parameters(model),
        "strict": True,
    }


class BoundedAgentOrchestrator:
    """Run one finite model/tool loop; model output never directly mutates business state."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        model: AgentModel,
        *,
        tool_executor: ToolExecutor | None = None,
        prompts: PromptRegistry | None = None,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._uow_factory = uow_factory
        self._model = model
        self._tools = tool_executor or ToolExecutor(uow_factory)
        self._queries = QueryService(uow_factory)
        self._prompts = prompts or default_prompt_registry()
        self._clock = monotonic

    def run(self, principal: Principal, project_id: str, request: AgentRunInput) -> AgentRunView:
        principal.require_project(project_id, Role.PLANNER)
        if not self._model.available:
            raise ApplicationError(ErrorCode.MODEL_UNAVAILABLE, "Agent model is not configured.")
        self._queries.get_task(principal, project_id, request.allocation_task_id)
        prompt = self._prompts.get("tool_orchestrator", "1.0.0")
        goal_hash = canonical_hash(request.goal)
        budget_payload = request.budget.model_dump(mode="json")

        with self._uow_factory() as uow:
            existing = uow.agent_runs.get(request.run_id)
            if existing is not None:
                if (
                    existing.project_id == project_id
                    and existing.allocation_task_id == request.allocation_task_id
                    and existing.goal_hash == goal_hash
                    and existing.budget_payload == budget_payload
                ):
                    return self._view(principal, project_id, request.run_id)
                raise ApplicationError(
                    ErrorCode.CONFLICT, "Agent run ID was already used for different content."
                )
            now = datetime.now(UTC)
            run = AgentRunRecord(
                run_id=request.run_id,
                project_id=project_id,
                allocation_task_id=request.allocation_task_id,
                caller_user_id=principal.user_id,
                model_id=self._model.model_id,
                status=AgentRunStatus.RUNNING,
                goal_hash=goal_hash,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
                prompt_content_sha256=prompt.content_sha256,
                budget_payload=budget_payload,
                usage_payload=self._usage(0, 0, 0, 0, 0),
                previous_response_id=None,
                final_payload=None,
                error_code=None,
                created_at=now,
                updated_at=now,
            )
            uow.agent_runs.add(run)
            uow.agent_runs.add_trace(
                AgentTraceRecord(
                    run_id=run.run_id,
                    sequence=1,
                    event_type="run_started",
                    payload={"goal_hash": goal_hash, "model_id": self._model.model_id},
                    created_at=now,
                )
            )
            uow.commit()

        started = self._clock()
        sequence = 1
        model_turns = tool_calls = input_tokens = output_tokens = cost_microusd = 0
        previous_response_id: str | None = None
        model_input: str | tuple[dict[str, Any], ...] = json.dumps(
            {
                "project_id": project_id,
                "allocation_task_id": request.allocation_task_id,
                "goal": request.goal,
            },
            ensure_ascii=False,
        )
        signatures: set[str] = set()
        tools = self._tools.registry.openai_tools() + (
            _control_tool(
                "finish_agent_run",
                "End the bounded run with an explicit non-mutating terminal outcome.",
                FinishAgentRunInput,
            ),
            _control_tool(
                "request_human_approval",
                "Pause before a save, reservation, or approval action; this never executes it.",
                RequestHumanApprovalInput,
            ),
        )

        while model_turns < request.budget.max_model_turns:
            elapsed_ms = round((self._clock() - started) * 1_000)
            if elapsed_ms >= request.budget.max_elapsed_ms:
                return self._finish(
                    run,
                    AgentRunStatus.BUDGET_EXHAUSTED,
                    sequence,
                    self._usage(
                        model_turns, tool_calls, input_tokens, output_tokens, cost_microusd
                    ),
                    previous_response_id,
                    error_code="elapsed_budget_exhausted",
                )
            remaining_seconds = max(0.001, (request.budget.max_elapsed_ms - elapsed_ms) / 1_000)
            try:
                turn = self._model.respond(
                    AgentModelRequest(
                        instructions=prompt.system_prompt,
                        input=model_input,
                        previous_response_id=previous_response_id,
                        tools=tools,
                        timeout_seconds=remaining_seconds,
                        max_output_tokens=request.budget.max_output_tokens_per_turn,
                    )
                )
            except AgentModelTimeout:
                return self._finish(
                    run,
                    AgentRunStatus.BUDGET_EXHAUSTED,
                    sequence,
                    self._usage(
                        model_turns, tool_calls, input_tokens, output_tokens, cost_microusd
                    ),
                    previous_response_id,
                    error_code="agent_timeout",
                )
            except Exception as error:
                return self._finish(
                    run,
                    AgentRunStatus.TOOL_ERROR,
                    sequence,
                    self._usage(
                        model_turns, tool_calls, input_tokens, output_tokens, cost_microusd
                    ),
                    previous_response_id,
                    error_code=f"model_invocation_failed:{type(error).__name__}",
                )

            model_turns += 1
            input_tokens += turn.usage.input_tokens
            output_tokens += turn.usage.output_tokens
            cost_microusd += turn.usage.cost_microusd
            previous_response_id = turn.response_id
            sequence += 1
            usage = self._usage(model_turns, tool_calls, input_tokens, output_tokens, cost_microusd)
            self._append_trace(
                run.run_id,
                sequence,
                "model_response",
                {
                    "response_id": turn.response_id,
                    "function_calls": [
                        {"call_id": item.call_id, "name": item.name} for item in turn.function_calls
                    ],
                    "output_text_hash": canonical_hash(turn.output_text or ""),
                    "usage": turn.usage.model_dump(mode="json"),
                },
            )
            if input_tokens + output_tokens > request.budget.max_total_tokens:
                return self._finish(
                    run,
                    AgentRunStatus.BUDGET_EXHAUSTED,
                    sequence,
                    usage,
                    previous_response_id,
                    error_code="token_budget_exhausted",
                )
            if cost_microusd > request.budget.max_cost_microusd:
                return self._finish(
                    run,
                    AgentRunStatus.BUDGET_EXHAUSTED,
                    sequence,
                    usage,
                    previous_response_id,
                    error_code="cost_budget_exhausted",
                )
            if round((self._clock() - started) * 1_000) >= request.budget.max_elapsed_ms:
                return self._finish(
                    run,
                    AgentRunStatus.BUDGET_EXHAUSTED,
                    sequence,
                    usage,
                    previous_response_id,
                    error_code="elapsed_budget_exhausted",
                )
            if len(turn.function_calls) != 1:
                return self._finish(
                    run,
                    AgentRunStatus.TOOL_ERROR,
                    sequence,
                    usage,
                    previous_response_id,
                    error_code="model_protocol_error",
                )

            call = turn.function_calls[0]
            try:
                arguments = json.loads(call.arguments_json)
                if not isinstance(arguments, dict):
                    raise ValueError("function arguments must be an object")
            except (json.JSONDecodeError, ValueError):
                return self._finish(
                    run,
                    AgentRunStatus.TOOL_ERROR,
                    sequence,
                    usage,
                    previous_response_id,
                    error_code="invalid_function_arguments",
                )
            signature = canonical_hash({"name": call.name, "arguments": arguments})
            if signature in signatures:
                return self._finish(
                    run,
                    AgentRunStatus.TOOL_ERROR,
                    sequence,
                    usage,
                    previous_response_id,
                    error_code="duplicate_tool_call",
                )
            signatures.add(signature)

            if call.name == "finish_agent_run":
                try:
                    terminal = FinishAgentRunInput.model_validate(arguments)
                except ValidationError:
                    return self._finish(
                        run,
                        AgentRunStatus.TOOL_ERROR,
                        sequence,
                        usage,
                        previous_response_id,
                        error_code="invalid_terminal_payload",
                    )
                return self._finish(
                    run,
                    AgentRunStatus(terminal.status),
                    sequence,
                    usage,
                    previous_response_id,
                    final_payload=terminal.model_dump(mode="json"),
                )

            if call.name == "request_human_approval":
                try:
                    approval_input = RequestHumanApprovalInput.model_validate(arguments)
                except ValidationError:
                    return self._finish(
                        run,
                        AgentRunStatus.TOOL_ERROR,
                        sequence,
                        usage,
                        previous_response_id,
                        error_code="invalid_approval_payload",
                    )
                now = datetime.now(UTC)
                approval = AgentApprovalRequestRecord(
                    approval_request_id=f"APR-{canonical_hash(run.run_id)[:24]}",
                    run_id=run.run_id,
                    action=approval_input.action,
                    resource_id=approval_input.resource_id,
                    reason=approval_input.reason,
                    status=AgentApprovalStatus.PENDING,
                    requested_at=now,
                )
                with self._uow_factory() as uow:
                    uow.agent_runs.add_approval(approval)
                    uow.commit()
                return self._finish(
                    run,
                    AgentRunStatus.AWAITING_APPROVAL,
                    sequence,
                    usage,
                    previous_response_id,
                    final_payload={
                        "approval_request_id": approval.approval_request_id,
                        "action": approval.action.value,
                        "resource_id": approval.resource_id,
                        "execution_performed": False,
                    },
                )

            if tool_calls >= request.budget.max_tool_calls:
                return self._finish(
                    run,
                    AgentRunStatus.BUDGET_EXHAUSTED,
                    sequence,
                    usage,
                    previous_response_id,
                    error_code="tool_call_budget_exhausted",
                )
            result = self._tools.execute(
                principal,
                project_id=project_id,
                run_id=run.run_id,
                tool_call_id=call.call_id,
                tool_name=call.name,
                arguments=arguments,
            )
            tool_calls += 1
            sequence += 1
            self._append_trace(
                run.run_id,
                sequence,
                "tool_result",
                {
                    "tool_call_id": result.tool_call_id,
                    "tool_name": result.tool_name,
                    "status": result.status,
                    "result_hash": result.result_hash,
                    "error_code": result.error.code if result.error else None,
                },
            )
            model_input = (
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
                },
            )

        return self._finish(
            run,
            AgentRunStatus.BUDGET_EXHAUSTED,
            sequence,
            self._usage(model_turns, tool_calls, input_tokens, output_tokens, cost_microusd),
            previous_response_id,
            error_code="model_turn_budget_exhausted",
        )

    def get(self, principal: Principal, project_id: str, run_id: str) -> AgentRunView:
        principal.require_project(project_id)
        return self._view(principal, project_id, run_id)

    def resolve_approval(
        self,
        principal: Principal,
        project_id: str,
        run_id: str,
        request: ApprovalResolutionInput,
    ) -> AgentRunView:
        view = self._view(principal, project_id, run_id)
        if view.approval is None or view.approval.status != AgentApprovalStatus.PENDING:
            raise ApplicationError(ErrorCode.CONFLICT, "Agent run has no pending approval request.")
        required_role = (
            Role.REVIEWER if view.approval.action == AgentActionType.APPROVE_PLAN else Role.PLANNER
        )
        principal.require_project(project_id, required_role)
        now = datetime.now(UTC)
        approval_status = AgentApprovalStatus(request.decision)
        run_status = (
            AgentRunStatus.APPROVAL_GRANTED
            if approval_status == AgentApprovalStatus.APPROVED
            else AgentRunStatus.APPROVAL_REJECTED
        )
        approval = replace(
            view.approval,
            status=approval_status,
            decided_by=principal.user_id,
            decided_at=now,
            note=request.note,
        )
        run = replace(
            view.run,
            status=run_status,
            final_payload={
                **(view.run.final_payload or {}),
                "approval_decision": approval_status.value,
                "execution_performed": False,
            },
            updated_at=now,
        )
        with self._uow_factory() as uow:
            uow.agent_runs.update_approval(approval)
            uow.agent_runs.update(run)
            uow.agent_runs.add_trace(
                AgentTraceRecord(
                    run_id=run_id,
                    sequence=len(view.trace) + 1,
                    event_type="approval_resolved",
                    payload={
                        "approval_request_id": approval.approval_request_id,
                        "decision": approval_status.value,
                        "decided_by": principal.user_id,
                        "execution_performed": False,
                    },
                    created_at=now,
                )
            )
            uow.commit()
        return self._view(principal, project_id, run_id)

    @staticmethod
    def _usage(
        model_turns: int,
        tool_calls: int,
        input_tokens: int,
        output_tokens: int,
        cost_microusd: int,
    ) -> dict[str, int]:
        return {
            "model_turns": model_turns,
            "tool_calls": tool_calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_microusd": cost_microusd,
        }

    def _append_trace(
        self, run_id: str, sequence: int, event_type: str, payload: dict[str, Any]
    ) -> None:
        with self._uow_factory() as uow:
            uow.agent_runs.add_trace(
                AgentTraceRecord(
                    run_id=run_id,
                    sequence=sequence,
                    event_type=event_type,
                    payload=payload,
                    created_at=datetime.now(UTC),
                )
            )
            uow.commit()

    def _finish(
        self,
        original: AgentRunRecord,
        status: AgentRunStatus,
        sequence: int,
        usage: dict[str, int],
        previous_response_id: str | None,
        *,
        final_payload: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> AgentRunView:
        now = datetime.now(UTC)
        run = replace(
            original,
            status=status,
            usage_payload=usage,
            previous_response_id=previous_response_id,
            final_payload=final_payload,
            error_code=error_code,
            updated_at=now,
        )
        with self._uow_factory() as uow:
            uow.agent_runs.update(run)
            uow.agent_runs.add_trace(
                AgentTraceRecord(
                    run_id=run.run_id,
                    sequence=sequence + 1,
                    event_type="run_finished",
                    payload={"status": status.value, "error_code": error_code},
                    created_at=now,
                )
            )
            uow.commit()
        principal = Principal(
            user_id=original.caller_user_id,
            roles=frozenset({Role.READER}),
            project_ids=frozenset({original.project_id}),
        )
        return self._view(principal, original.project_id, original.run_id)

    def _view(self, principal: Principal, project_id: str, run_id: str) -> AgentRunView:
        principal.require_project(project_id)
        with self._uow_factory() as uow:
            run = uow.agent_runs.get(run_id)
            if run is None or run.project_id != project_id:
                raise ApplicationError(ErrorCode.NOT_FOUND, "Agent run was not found.")
            return AgentRunView(
                run=run,
                trace=uow.agent_runs.list_traces(run_id),
                approval=uow.agent_runs.get_approval(run_id),
            )
