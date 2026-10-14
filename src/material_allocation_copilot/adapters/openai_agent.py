"""OpenAI Responses adapter for the bounded agent runtime."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from material_allocation_copilot.application.agent_runtime import (
    AgentFunctionCall,
    AgentModelInvocationError,
    AgentModelRequest,
    AgentModelTimeout,
    AgentModelTurn,
    AgentUsage,
)


def _value(item: object, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


class OpenAIAgentModel:
    """Translate the application protocol to Responses API function calls."""

    available = True

    def __init__(
        self,
        client: object,
        model_id: str,
        *,
        input_cost_per_million_usd: Decimal,
        output_cost_per_million_usd: Decimal,
    ) -> None:
        self._client = client
        self._model_id = model_id
        self._input_rate = input_cost_per_million_usd
        self._output_rate = output_cost_per_million_usd

    @property
    def model_id(self) -> str:
        return self._model_id

    def respond(self, request: AgentModelRequest) -> AgentModelTurn:
        responses = self._client.responses
        try:
            response = responses.create(
                model=self._model_id,
                instructions=request.instructions,
                input=list(request.input) if isinstance(request.input, tuple) else request.input,
                previous_response_id=request.previous_response_id,
                tools=list(request.tools),
                parallel_tool_calls=False,
                max_output_tokens=request.max_output_tokens,
                timeout=request.timeout_seconds,
            )
        except Exception as error:
            if isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower():
                raise AgentModelTimeout("OpenAI response timed out.") from error
            raise AgentModelInvocationError("OpenAI response failed.") from error

        function_calls = tuple(
            AgentFunctionCall(
                call_id=str(_value(item, "call_id")),
                name=str(_value(item, "name")),
                arguments_json=str(_value(item, "arguments", "{}")),
            )
            for item in (_value(response, "output", ()) or ())
            if _value(item, "type") == "function_call"
        )
        usage = _value(response, "usage")
        input_tokens = int(_value(usage, "input_tokens", 0) or 0)
        output_tokens = int(_value(usage, "output_tokens", 0) or 0)
        # USD per million tokens converts numerically to micro-USD per token.
        cost = (
            Decimal(input_tokens) * self._input_rate + Decimal(output_tokens) * self._output_rate
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return AgentModelTurn(
            response_id=str(_value(response, "id")),
            function_calls=function_calls,
            output_text=_value(response, "output_text"),
            usage=AgentUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_microusd=int(cost),
            ),
        )
