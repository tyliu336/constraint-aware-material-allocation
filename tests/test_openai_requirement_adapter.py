from types import SimpleNamespace

import pytest

from material_allocation_copilot.adapters.openai_requirements import OpenAIRequirementModel
from material_allocation_copilot.application.semantic_requirements import (
    ModelInvocationError,
    ModelOutputError,
)
from material_allocation_copilot.domain import ModelRequirementInterpretation
from material_allocation_copilot.prompts import default_prompt_registry


class FakeResponses:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return SimpleNamespace(output_parsed=self.result)


def test_openai_adapter_uses_responses_structured_output_without_tools():
    parsed = ModelRequirementInterpretation(
        summary="No slots yet.",
        slots=(),
        soft_preferences=(),
        issues=(),
        clarification_questions=(),
    )
    responses = FakeResponses(parsed)
    adapter = OpenAIRequirementModel(SimpleNamespace(responses=responses), "configured-model")
    prompt = default_prompt_registry().get("requirement_parser", "1.0.0")

    result = adapter.generate_structured(
        prompt=prompt,
        user_input="Need a beam.",
        output_type=ModelRequirementInterpretation,
    )

    assert result is parsed
    assert responses.kwargs["model"] == "configured-model"
    assert responses.kwargs["text_format"] is ModelRequirementInterpretation
    assert "tools" not in responses.kwargs


def test_openai_adapter_distinguishes_provider_failure_from_empty_structured_output():
    prompt = default_prompt_registry().get("requirement_parser", "1.0.0")
    failed = OpenAIRequirementModel(
        SimpleNamespace(responses=FakeResponses(error=RuntimeError("secret provider detail"))),
        "configured-model",
    )
    with pytest.raises(ModelInvocationError) as captured:
        failed.generate_structured(
            prompt=prompt,
            user_input="Need a beam.",
            output_type=ModelRequirementInterpretation,
        )
    assert "secret provider detail" not in str(captured.value)

    empty = OpenAIRequirementModel(
        SimpleNamespace(responses=FakeResponses(result=None)), "configured-model"
    )
    with pytest.raises(ModelOutputError):
        empty.generate_structured(
            prompt=prompt,
            user_input="Need a beam.",
            output_type=ModelRequirementInterpretation,
        )
