"""Schema-bound LLM interpretation for the simple workflow's soft preferences."""

from pydantic import ValidationError

from material_allocation_copilot.application.contracts import PreferenceInterpretationInput
from material_allocation_copilot.application.semantic_requirements import (
    ModelInvocationError,
    ModelOutputError,
    StructuredRequirementModel,
)
from material_allocation_copilot.application.services import UnitOfWorkFactory, validate_input
from material_allocation_copilot.domain import (
    ModelPreferenceInterpretation,
    PreferenceInterpretationView,
)
from material_allocation_copilot.errors import ApplicationError, ErrorCode
from material_allocation_copilot.prompts import PromptRegistry

MAX_PREFERENCE_MODEL_ATTEMPTS = 2


class PreferenceInterpretationService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        model: StructuredRequirementModel,
        prompts: PromptRegistry,
    ) -> None:
        self._uow_factory = uow_factory
        self._model = model
        self._prompts = prompts

    def interpret(self, request: PreferenceInterpretationInput) -> PreferenceInterpretationView:
        request = validate_input(PreferenceInterpretationInput, request)
        with self._uow_factory() as uow:
            if uow.projects.get(request.project_id) is None:
                raise ApplicationError(ErrorCode.NOT_FOUND, "Project was not found.")
        if not self._model.available:
            raise ApplicationError(
                ErrorCode.MODEL_UNAVAILABLE,
                "Preference interpretation model is not configured.",
            )

        prompt = self._prompts.get("preference_interpreter", "1.1.0")
        last_error = "model output did not satisfy the preference schema"
        for _ in range(MAX_PREFERENCE_MODEL_ATTEMPTS):
            try:
                raw = self._model.generate_structured(
                    prompt=prompt,
                    user_input=request.prompt,
                    output_type=ModelPreferenceInterpretation,
                )
                parsed = ModelPreferenceInterpretation.model_validate(raw)
                return PreferenceInterpretationView(
                    summary=parsed.summary,
                    preferences=parsed.preferences,
                    model_id=self._model.model_id,
                    prompt_version=prompt.version,
                    prompt_content_sha256=prompt.content_sha256,
                )
            except ModelInvocationError as error:
                raise ApplicationError(
                    ErrorCode.MODEL_INVOCATION_FAILED,
                    "Preference interpretation model call failed.",
                    context={"model_id": self._model.model_id},
                ) from error
            except (ValidationError, ModelOutputError, ValueError, TypeError) as error:
                last_error = str(error)[:1000] or type(error).__name__
        raise ApplicationError(
            ErrorCode.INPUT_INVALID,
            "The model could not return valid preference weights.",
            context={"model_id": self._model.model_id, "detail": last_error},
        )
