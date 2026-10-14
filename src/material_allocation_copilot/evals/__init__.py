"""Offline, deterministic evaluation utilities for semantic AI boundaries."""

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from material_allocation_copilot.evals.runner import EvalCaseResult, EvalSuiteReport

__all__ = ["EvalCaseResult", "EvalSuiteReport", "load_eval_cases", "run_eval_suite"]


def load_eval_cases(path: Path | None = None):
    from material_allocation_copilot.evals.runner import DEFAULT_EVAL_DATASET, load_eval_cases

    return load_eval_cases(DEFAULT_EVAL_DATASET if path is None else path)


def run_eval_suite(path: Path | None = None):
    from material_allocation_copilot.evals.runner import DEFAULT_EVAL_DATASET, run_eval_suite

    return run_eval_suite(DEFAULT_EVAL_DATASET if path is None else path)


def __getattr__(name: str):
    if name in {"EvalCaseResult", "EvalSuiteReport"}:
        from material_allocation_copilot.evals import runner

        return getattr(runner, name)
    raise AttributeError(name)
