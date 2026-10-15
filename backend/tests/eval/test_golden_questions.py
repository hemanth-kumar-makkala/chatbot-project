"""Validation tests for the labelled golden evaluation benchmark questions.

Ensures that evals/golden/questions.json adheres to all evaluation requirements:
- At least 100 questions total
- Per-category coverage minimums
- Grounded expected answers (matching digital page text or scanned key_facts)
- Refusal rules for NO_ANSWER and CROSS_TENANT
- Conflict mode verification with dual evidence
- Prompt injection safety guardrails
- Unique IDs and deterministic formatting
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import openpyxl
import pypdf
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
GOLDEN_DIR = REPO_ROOT / "evals" / "golden"
QUESTIONS_PATH = GOLDEN_DIR / "questions.json"
MANIFEST_PATH = GOLDEN_DIR / "manifest.json"
DOCS_DIR = GOLDEN_DIR / "docs"

CATEGORY_MINIMUMS = {
    "TEXT_FACT": 20,
    "TABLE_LOOKUP": 15,
    "VISUAL_RECOVERY": 15,
    "LENGTH": 8,
    "CONFLICT": 8,
    "NO_ANSWER": 15,
    "INJECTION": 8,
    "CROSS_TENANT": 10,
}

ALLOWED_MODES = {"answer", "refuse", "conflict"}


def _normalize(text: str) -> str:
    """Normalize text by lowercasing and collapsing whitespace."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _load_manifest_docs() -> dict[str, dict[str, Any]]:
    if not MANIFEST_PATH.exists():
        pytest.fail(f"Manifest not found at {MANIFEST_PATH}")
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        data = json.load(f)
    docs_list = data["documents"] if isinstance(data, dict) and "documents" in data else data
    return {d["id"]: d for d in docs_list}


def _load_questions() -> list[dict[str, Any]]:
    if not QUESTIONS_PATH.exists():
        pytest.fail(f"Questions file not found at {QUESTIONS_PATH}")
    with open(QUESTIONS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "questions" in data:
        return cast(list[dict[str, Any]], data["questions"])
    if isinstance(data, list):
        return cast(list[dict[str, Any]], data)
    pytest.fail("questions.json must be a list or dict with 'questions' list")


def _get_page_text(doc: dict[str, Any], page_num: int) -> str:
    """Extract text from a digital document page for ground-truth verification."""
    rel_path = doc.get("relative_path", doc["file_name"])
    file_path = DOCS_DIR / str(rel_path)
    ext = file_path.suffix.lower()

    if ext == ".pdf":
        reader = pypdf.PdfReader(file_path)
        if 1 <= page_num <= len(reader.pages):
            return reader.pages[page_num - 1].extract_text() or ""
        return ""

    if ext == ".csv":
        return file_path.read_text(encoding="utf-8")

    if ext == ".xlsx":
        wb = openpyxl.load_workbook(file_path, data_only=True)
        cells = []
        for sheet in wb.worksheets:
            for row in sheet.iter_rows(values_only=True):
                for val in row:
                    if val is not None:
                        cells.append(str(val))
        return " ".join(cells)

    return ""


def test_questions_file_exists_and_parses():
    """Verify evals/golden/questions.json exists and parses cleanly."""
    questions = _load_questions()
    assert isinstance(questions, list)


def test_at_least_100_questions():
    """Verify there are at least 100 questions in total."""
    questions = _load_questions()
    assert len(questions) >= 100, f"Expected at least 100 questions, got {len(questions)}"


def test_unique_question_ids():
    """Verify every question has a unique ID."""
    questions = _load_questions()
    seen_ids = set()
    for q in questions:
        qid = q.get("id")
        assert qid, "Question missing id"
        assert qid not in seen_ids, f"Duplicate question id: {qid}"
        seen_ids.add(qid)


def test_per_category_minimums():
    """Verify every category satisfies its required minimum question count."""
    questions = _load_questions()
    counts: dict[str, int] = {}
    for q in questions:
        cat = q.get("category", "UNKNOWN")
        counts[cat] = counts.get(cat, 0) + 1

    for cat, min_count in CATEGORY_MINIMUMS.items():
        actual = counts.get(cat, 0)
        assert actual >= min_count, (
            f"Category {cat} has {actual} questions, minimum required is {min_count}"
        )


def test_question_entry_schema():
    """Verify required schema fields, allowed modes, and tenant values."""
    questions = _load_questions()
    required_keys = [
        "id",
        "category",
        "tenant",
        "question",
        "expected_mode",
        "expected_answer",
        "evidence",
        "must_not_contain",
        "notes",
    ]

    for q in questions:
        for k in required_keys:
            assert k in q, f"Question {q.get('id')} missing required key '{k}'"

        assert q["tenant"] in {"A", "B"}, f"Invalid tenant {q['tenant']} in {q['id']}"
        assert q["expected_mode"] in ALLOWED_MODES, (
            f"Invalid expected_mode {q['expected_mode']} in {q['id']}"
        )
        assert isinstance(q["question"], str) and len(q["question"].strip()) > 0
        assert isinstance(q["must_not_contain"], list)
        assert isinstance(q["notes"], str)
        assert isinstance(q["evidence"], list)

        # Mode-specific constraints
        if q["expected_mode"] == "refuse":
            assert q["expected_answer"] is None, (
                f"Refusal question {q['id']} must have expected_answer=null"
            )
            assert len(q["evidence"]) == 0, (
                f"Refusal question {q['id']} must have empty evidence list"
            )
        elif q["expected_mode"] == "conflict":
            assert isinstance(q["expected_answer"], str) and len(q["expected_answer"].strip()) > 0
            assert len(q["evidence"]) >= 2, (
                f"Conflict question {q['id']} must cite at least 2 conflicting evidence pages"
            )
        elif q["expected_mode"] == "answer":
            assert isinstance(q["expected_answer"], str) and len(q["expected_answer"].strip()) > 0
            assert len(q["evidence"]) >= 1, (
                f"Answer question {q['id']} must cite at least 1 evidence page"
            )


def test_evidence_references_exist_in_manifest():
    """Verify every cited evidence document and page exists in manifest."""
    manifest_docs = _load_manifest_docs()
    questions = _load_questions()

    for q in questions:
        for ev in q["evidence"]:
            assert "document_id" in ev and "page" in ev, f"Malformed evidence in {q['id']}: {ev}"
            doc_id = ev["document_id"]
            page = ev["page"]
            assert doc_id in manifest_docs, (
                f"Evidence document {doc_id} cited in {q['id']} does not exist in manifest"
            )
            max_pages = manifest_docs[doc_id]["page_count"]
            assert 1 <= page <= max_pages, (
                f"Evidence page {page} out of range (1..{max_pages}) for doc {doc_id} in {q['id']}"
            )


def test_expected_answer_grounded_in_evidence():
    """Verify expected answers appear in the text of digital pages or key_facts of scanned faxes."""
    manifest_docs = _load_manifest_docs()
    questions = _load_questions()

    for q in questions:
        if q["expected_mode"] != "answer":
            continue

        ans = q["expected_answer"]
        assert isinstance(ans, str)
        norm_ans = _normalize(ans)

        # At least one cited evidence page must contain the expected answer
        found = False
        for ev in q["evidence"]:
            doc_id = ev["document_id"]
            page = ev["page"]
            doc = manifest_docs[doc_id]

            if doc["category"] == "VISUAL_RECOVERY":
                # Scanned faxes have no text layer: check manifest key_facts for that page
                page_facts = [f["fact"] for f in doc.get("key_facts", []) if f.get("page") == page]
                facts_text = _normalize(" ".join(page_facts))
                if norm_ans in facts_text:
                    found = True
                    break
            else:
                # Digital PDFs, CSV, XLSX
                page_text = _normalize(_get_page_text(doc, page))
                if norm_ans in page_text:
                    found = True
                    break

        assert found, (
            f"Expected answer '{ans}' in question {q['id']} was not found in "
            f"cited evidence: {q['evidence']}"
        )


BANNED_QUESTION_HINTS = [
    # Document filenames / extensions
    "clean_fax",
    "skewed_fax",
    "faint_fax",
    "accepted_plans",
    "payer_contact_table",
    "fee_schedule",
    "prior_auth_guide",
    "eligibility_checklist",
    "clinical_protocols_manual",
    "compliance_notice",
    "front_desk_policy",
    ".csv",
    ".xlsx",
    ".pdf",
    # Visual conditions
    "clean fax",
    "skewed",
    "tilted",
    "faint",
    # Page / location references
    "page 1",
    "page 2",
    "page 12",
    "page 13",
    "page 14",
    "page 15",
    "first page",
    "second page",
    "on page",
    "chapter 12",
    "chapter 13",
    "chapter 14",
    "chapter 15",
]

BANNED_GENERIC_GUARDRAILS = [
    "available",
    "confirmed",
    "guarantee",
    "patient data",
]


def test_visual_recovery_faint_page_coverage():
    """Verify there are visual recovery questions covering faint page 2."""
    questions = _load_questions()
    faint_p2_questions = [
        q
        for q in questions
        if q["category"] == "VISUAL_RECOVERY"
        and any(
            ev.get("document_id") == "doc_faint_fax_2page" and ev.get("page") == 2
            for ev in q.get("evidence", [])
        )
    ]
    assert len(faint_p2_questions) >= 3, (
        f"Expected at least 3 questions covering faint page 2, found {len(faint_p2_questions)}"
    )


def test_no_question_text_contains_hint_words():
    """Verify no question text contains document names, visual conditions, or location hints."""
    questions = _load_questions()
    for q in questions:
        q_lower = q["question"].lower()
        for hint in BANNED_QUESTION_HINTS:
            assert hint not in q_lower, (
                f"Question {q['id']} contains banned hint '{hint}': {q['question']}"
            )


def test_no_answer_and_injection_guardrails_no_banned_generic_words():
    """Verify NO_ANSWER and INJECTION must_not_contain lists contain no generic banned words."""
    questions = _load_questions()
    for q in questions:
        if q["category"] in {"NO_ANSWER", "INJECTION"}:
            for item in q.get("must_not_contain", []):
                item_lower = item.strip().lower()
                for banned in BANNED_GENERIC_GUARDRAILS:
                    assert item_lower != banned, (
                        f"Question {q['id']} in category {q['category']} contains banned "
                        f"generic word '{banned}' in must_not_contain: {item}"
                    )


def test_injection_questions_guardrails():
    """Verify prompt-injection questions specify appropriate must_not_contain guardrails."""
    questions = _load_questions()
    injection_qs = [q for q in questions if q["category"] == "INJECTION"]
    assert len(injection_qs) >= 8

    for q in injection_qs:
        mnc_joined = " ".join(q["must_not_contain"]).lower()
        assert "ignore previous instructions" in mnc_joined, (
            f"Injection question {q['id']} must guard against prompt injection phrase"
        )
        assert any(name in mnc_joined for name in ["alex morgan", "jordan taylor"]), (
            f"Injection question {q['id']} must_not_contain must include specific patient names"
        )
        for item in q["must_not_contain"]:
            assert item.lower() != "patient data", (
                f"Injection question {q['id']} must not contain generic phrase 'patient data'"
            )


def test_cross_tenant_questions_guardrails():
    """Verify cross-tenant questions refuse and guard against other tenant's identifiers."""
    questions = _load_questions()
    cross_qs = [q for q in questions if q["category"] == "CROSS_TENANT"]
    assert len(cross_qs) >= 10

    for q in cross_qs:
        assert q["expected_mode"] == "refuse"
        assert q["expected_answer"] is None
        assert len(q["evidence"]) == 0
        assert len(q["must_not_contain"]) >= 1, (
            f"Cross-tenant question {q['id']} must list prohibited facts in must_not_contain"
        )


def test_conflict_questions_dual_evidence():
    """Verify all conflict questions cite both conflicting policy pages with conflict mode."""
    questions = _load_questions()
    conflict_qs = [q for q in questions if q["category"] == "CONFLICT"]
    assert len(conflict_qs) >= 8

    for q in conflict_qs:
        assert q["expected_mode"] == "conflict"
        doc_ids = {ev["document_id"] for ev in q["evidence"]}
        assert "doc_front_desk_policy_v1" in doc_ids and "doc_front_desk_policy_v2" in doc_ids, (
            f"Conflict question {q['id']} must cite both policy v1 and v2"
        )


def test_conflict_questions_cover_both_conflicting_facts():
    """Verify conflict questions split across both facts (fax and arrival window)."""
    questions = _load_questions()
    conflict_qs = [q for q in questions if q["category"] == "CONFLICT"]
    assert len(conflict_qs) >= 8

    fax_conflicts = [
        q
        for q in conflict_qs
        if "fax" in q["question"].lower() or "555-0101" in (q.get("expected_answer") or "")
    ]
    arrival_conflicts = [
        q
        for q in conflict_qs
        if any(
            w in q["question"].lower()
            for w in ["arrival", "arrive", "check-in", "early", "advance"]
        )
        or "15 minutes" in (q.get("expected_answer") or "")
    ]

    assert len(fax_conflicts) >= 3, (
        f"Expected at least 3 fax conflict questions, got {len(fax_conflicts)}"
    )
    assert len(arrival_conflicts) >= 3, (
        f"Expected at least 3 arrival window conflict questions, got {len(arrival_conflicts)}"
    )
