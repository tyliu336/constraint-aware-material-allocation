from material_allocation_copilot.evals import load_eval_cases, run_eval_suite


def test_frozen_eval_dataset_covers_all_declared_categories():
    cases = load_eval_cases()

    assert len(cases) == 9
    assert {item.category for item in cases} == {
        "requirement_parsing",
        "clarification",
        "explanation_consistency",
        "prompt_injection",
    }


def test_offline_eval_baseline_passes_without_calling_a_model():
    report = run_eval_suite()

    assert report.total == 9
    assert report.passed == 9
    assert report.failed == 0
    assert report.score == 1
