"""Integration test measuring cross-encoder threshold.
Evaluates 5 TEXT_FACT vs 5 NO_ANSWER golden questions.

Proves Task 3.3 requirement:
RELIABLE_TEXT_THRESHOLD (0.25) cleanly separates strong evidence from non-answer questions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pypdf
import pytest

from app.core.graph import RELIABLE_TEXT_THRESHOLD, WEAK_EVIDENCE_THRESHOLD
from app.ports.types import RerankCandidate
from app.wiring import create_reranker


@pytest.mark.models
@pytest.mark.asyncio
async def test_threshold_measured_on_5_text_fact_vs_5_no_answer_golden_questions() -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    docs_dir = repo_root / "evals" / "golden" / "docs"
    questions_path = repo_root / "evals" / "golden" / "questions.json"

    # Prior auth guide page 1 text
    pdf_path = docs_dir / "prior_auth_guide.pdf"
    reader = pypdf.PdfReader(str(pdf_path))
    p1_text = reader.pages[0].extract_text()

    with open(questions_path, encoding="utf-8") as f:
        questions = json.load(f)

    tf_questions = [q for q in questions if q.get("category") == "TEXT_FACT"][:5]
    noa_questions = [q for q in questions if q.get("category") == "NO_ANSWER"][:5]

    reranker = create_reranker("desktop-gpu")
    candidate = RerankCandidate(
        candidate_id="chunk_prior_auth_p1",
        text=f"prior_auth_guide.pdf - Page 1\n\n{p1_text}",
    )

    tf_scores: list[float] = []
    for q in tf_questions:
        res = await reranker.rerank(q["question"], [candidate])
        tf_scores.append(res[0].score)

    noa_scores: list[float] = []
    for q in noa_questions:
        res = await reranker.rerank(q["question"], [candidate])
        noa_scores.append(res[0].score)

    min_tf = min(tf_scores)
    max_noa = max(noa_scores)

    # Assert clean separation across the threshold
    assert min_tf > RELIABLE_TEXT_THRESHOLD, (
        f"Min TEXT_FACT {min_tf} must exceed {RELIABLE_TEXT_THRESHOLD}"
    )
    assert max_noa < RELIABLE_TEXT_THRESHOLD, (
        f"Max NO_ANSWER {max_noa} must be below {RELIABLE_TEXT_THRESHOLD}"
    )
    assert WEAK_EVIDENCE_THRESHOLD < RELIABLE_TEXT_THRESHOLD
    assert min_tf - max_noa > 0.5, f"Separation margin {min_tf - max_noa} must exceed 0.5"
