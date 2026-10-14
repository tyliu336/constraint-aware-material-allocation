"""OpenAI Responses API adapter for schema-bound requirement interpretation."""

from __future__ import annotations

from pydantic import BaseModel

from material_allocation_copilot.application.semantic_requirements import (
    ModelInvocationError,
    ModelOutputError,
)
from material_allocation_copilot.prompts import PromptDefinition


class OpenAIRequirementModel:
    available = True

    def __init__(self, client: object, model_id: str) -> None:
        self._client = client
        self.model_id = model_id

    def generate_structured(
        self,
        *,
        prompt: PromptDefinition,
        user_input: str,
        output_type: type[BaseModel],
    ) -> object:
        try:
            response = self._client.responses.parse(  # type: ignore[attr-defined]
                model=self.model_id,
                input=[
                    {"role": "system", "content": prompt.system_prompt},
                    {"role": "user", "content": user_input},
                ],
                text_format=output_type,
            )
        except Exception as error:
            raise ModelInvocationError(f"model call failed: {type(error).__name__}") from error
        if response.output_parsed is None:
            raise ModelOutputError("model returned no schema-bound output")
        return response.output_parsed
