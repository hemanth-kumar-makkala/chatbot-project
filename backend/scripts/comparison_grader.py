"""Deterministic grading helpers for table-answer comparisons."""

from collections.abc import Collection


def table_answer_contains_only_expected_values(
    answer: str,
    *,
    expected_values: Collection[str],
    unexpected_values: Collection[str],
) -> bool:
    """Require every expected value and reject any value from unrelated tables."""
    normalized_answer = answer.casefold()
    if any(value.casefold() not in normalized_answer for value in expected_values):
        return False
    return not any(value.casefold() in normalized_answer for value in unexpected_values)
