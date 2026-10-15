"""Scoring package for synthetic golden benchmark evaluation."""

from .score import (
    contains_answer,
    normalize_text,
    score_results,
    score_run,
)

__all__ = [
    "contains_answer",
    "normalize_text",
    "score_results",
    "score_run",
]
