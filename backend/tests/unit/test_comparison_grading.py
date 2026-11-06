"""Regression coverage for strict table-answer grading."""

from scripts.comparison_grader import table_answer_contains_only_expected_values


def test_table_grader_rejects_unexpected_values_even_when_all_expected_are_present() -> None:
    expected = {"Plan Code", "APX-HMO-01", "Payer Name", "Apex Health Insurance"}
    answer = "Plan Code APX-HMO-01; Payer Name Apex Health Insurance; Diagnostic Imaging"

    assert not table_answer_contains_only_expected_values(
        answer,
        expected_values=expected,
        unexpected_values={"Diagnostic Imaging", "48 Hours"},
    )


def test_table_grader_accepts_answer_with_expected_values_only() -> None:
    assert table_answer_contains_only_expected_values(
        "Plan Code APX-HMO-01; Payer Name Apex Health Insurance",
        expected_values={"Plan Code", "APX-HMO-01", "Payer Name", "Apex Health Insurance"},
        unexpected_values={"Diagnostic Imaging", "48 Hours"},
    )
