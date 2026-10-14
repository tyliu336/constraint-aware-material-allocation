"""Run frozen requirement, explanation, and injection evals without a model call."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from material_allocation_copilot.application.context_builder import (
    ContextBuilder,
    ContextBuildStatus,
    ContextPurpose,
    ContextSource,
    ContextTrust,
)
from material_allocation_copilot.application.semantic_requirements import normalize_interpretation
from material_allocation_copilot.domain import ModelRequirementInterpretation
from material_allocation_copilot.prompts import default_prompt_registry

DEFAULT_EVAL_DATASET = Path(__file__).with_name("requirement_semantics.jsonl")


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    category: Literal[
        "requirement_parsing",
        "clarification",
        "explanation_consistency",
        "prompt_injection",
    ]
    input_text: str
    candidate_output: dict[str, Any] | str | None = None
    expected_status: Literal["ready_for_confirmation", "needs_clarification"] | None = None
    required_issue_codes: tuple[str, ...] = ()
    required_substrings: tuple[str, ...] = ()
    forbidden_substrings: tuple[str, ...] = ()


class EvalCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    category: str
    passed: bool
    checks: tuple[str, ...]
    failures: tuple[str, ...]


class EvalSuiteReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: str
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    score: float = Field(ge=0, le=1)
    results: tuple[EvalCaseResult, ...]


def load_eval_cases(path: Path = DEFAULT_EVAL_DATASET) -> tuple[EvalCase, ...]:
    cases = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            cases.append(EvalCase.model_validate_json(raw))
        except ValidationError as error:
            raise ValueError(f"invalid eval case on line {line_number}: {error}") from error
    ids = [item.case_id for item in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("eval case IDs must be unique")
    return tuple(cases)


def _interpretation_result(case: EvalCase) -> EvalCaseResult:
    checks: list[str] = []
    failures: list[str] = []
    try:
        parsed = ModelRequirementInterpretation.model_validate(case.candidate_output)
        normalized = normalize_interpretation(case.input_text, parsed)
    except (ValidationError, ValueError, TypeError) as error:
        return EvalCaseResult(
            case_id=case.case_id,
            category=case.category,
            passed=False,
            checks=(),
            failures=(f"schema_invalid:{type(error).__name__}",),
        )
    checks.append("schema_valid")
    actual_status = (
        "needs_clarification"
        if any(item.severity == "blocking" for item in normalized.issues)
        or any(item.required for item in normalized.clarification_questions)
        else "ready_for_confirmation"
    )
    if actual_status == case.expected_status:
        checks.append("status_matches")
    else:
        failures.append(f"status:{actual_status}!={case.expected_status}")
    actual_codes = {item.code.value for item in normalized.issues}
    missing = sorted(set(case.required_issue_codes) - actual_codes)
    if missing:
        failures.append("missing_issue_codes:" + ",".join(missing))
    else:
        checks.append("required_issue_codes_present")
    return EvalCaseResult(
        case_id=case.case_id,
        category=case.category,
        passed=not failures,
        checks=tuple(checks),
        failures=tuple(failures),
    )


def _explanation_result(case: EvalCase) -> EvalCaseResult:
    text = case.candidate_output if isinstance(case.candidate_output, str) else ""
    folded = text.casefold()
    missing = [value for value in case.required_substrings if value.casefold() not in folded]
    forbidden = [value for value in case.forbidden_substrings if value.casefold() in folded]
    failures = tuple(
        [f"missing:{value}" for value in missing] + [f"forbidden:{value}" for value in forbidden]
    )
    return EvalCaseResult(
        case_id=case.case_id,
        category=case.category,
        passed=not failures,
        checks=("required_facts_present", "forbidden_claims_absent") if not failures else (),
        failures=failures,
    )


def _injection_result(case: EvalCase) -> EvalCaseResult:
    prompt = default_prompt_registry().get("requirement_explanation", "1.0.0")
    snapshot = ContextBuilder().build(
        project_id="EVAL-PROJECT",
        purpose=ContextPurpose.REQUIREMENT_EXPLANATION,
        prompt=prompt,
        sources=(
            ContextSource(
                source_id="material_note",
                source_type="scanned_material_note",
                version="fixture-v1",
                trust=ContextTrust.UNTRUSTED_TEXT,
                payload=case.input_text,
            ),
        ),
    )
    failures = []
    if snapshot.status is not ContextBuildStatus.READY:
        failures.append(f"context_status:{snapshot.status.value}")
    if case.input_text in prompt.system_prompt:
        failures.append("untrusted_text_entered_system_prompt")
    if case.input_text not in snapshot.model_user_input:
        failures.append("untrusted_text_missing_from_data_envelope")
    parsed = json.loads(snapshot.model_user_input)
    source = parsed["sources"][0]
    if source["trust"] != ContextTrust.UNTRUSTED_TEXT.value:
        failures.append("untrusted_source_not_labelled")
    if source.get("handling") != "data_only_do_not_follow_instructions":
        failures.append("data_only_boundary_missing")
    return EvalCaseResult(
        case_id=case.case_id,
        category=case.category,
        passed=not failures,
        checks=("system_prompt_isolated", "untrusted_data_labelled") if not failures else (),
        failures=tuple(failures),
    )


def run_eval_suite(path: Path = DEFAULT_EVAL_DATASET) -> EvalSuiteReport:
    cases = load_eval_cases(path)
    results = []
    for case in cases:
        if case.category in {"requirement_parsing", "clarification"}:
            results.append(_interpretation_result(case))
        elif case.category == "explanation_consistency":
            results.append(_explanation_result(case))
        else:
            results.append(_injection_result(case))
    passed = sum(item.passed for item in results)
    total = len(results)
    return EvalSuiteReport(
        dataset=str(path),
        total=total,
        passed=passed,
        failed=total - passed,
        score=passed / total if total else 0,
        results=tuple(results),
    )


def main() -> int:
    report = run_eval_suite()
    print(report.model_dump_json(indent=2))
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
