"""Conflict resolution and document version ordering (Task 2).

Enforces:
- When two versions of the same document conflict, answer from the newer version
  (by effective date or version number).
- Add one short line noting that an older version said something different,
  naming both files.
- If version order cannot be determined (or documents are different), return None
  to keep the current CONFLICTING behavior.
"""

from __future__ import annotations

import contextlib
import re
from datetime import datetime
from typing import NamedTuple


class DocumentVersionMetadata(NamedTuple):
    base_name: str
    version_num: float | None
    effective_date: datetime | None
    is_superseding: bool


def _extract_date(text: str) -> datetime | None:
    """Extract standard date string from document header/text."""
    # Pattern: Issued: March 1, 2026 / Effective Date: January 15, 2026 / 2026-03-01
    date_patterns = [
        r"(?:issued|effective(?:\s+date)?|date)[\s:]+([A-Za-z]+ \d{1,2}, \d{4})",
        r"(?:issued|effective(?:\s+date)?|date)[\s:]+(\d{4}-\d{2}-\d{2})",
        r"\b([A-Za-z]+ \d{1,2}, \d{4})\b",
    ]
    for pattern in date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            date_str = match.group(1).strip()
            for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue
    return None


def _normalize_base_name(filename_or_title: str) -> str:
    """Extract base document name stripped of extensions and version markers."""
    clean = filename_or_title.strip()
    # Strip common file extensions
    clean = re.sub(r"\.(pdf|docx?|xlsx?|csv|txt)$", "", clean, flags=re.IGNORECASE)
    # Strip version markers: _v1, -v2, v1.0, version 2, (Version 2.0 Revised)
    clean = re.sub(
        r"[\s_.-]*\(?v(?:er(?:sion)?)?[\s_.-]*\d+(?:\.\d+)?(?:\s+revised)?\)?",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    # Strip year markers if appended like _2026
    clean = re.sub(r"[\s_.-]*(?:19|20)\d{2}", "", clean)
    clean = re.sub(r"[\s_.-]+", " ", clean).strip().lower()
    return clean


def extract_document_version_metadata(
    doc_name: str,
    chunk_text: str,
) -> DocumentVersionMetadata:
    """Extract version number, effective date, and base document name."""
    combined = f"{doc_name}\n{chunk_text}"
    base_name = _normalize_base_name(doc_name)

    # Version number extraction (prefers filename v\d+ or text Version \d+(\.\d+)?)
    version_num: float | None = None
    fn_v_match = re.search(r"v(\d+(?:\.\d+)?)", doc_name, re.IGNORECASE)
    if fn_v_match:
        with contextlib.suppress(ValueError):
            version_num = float(fn_v_match.group(1))

    if version_num is None:
        text_v_match = re.search(r"version\s*(\d+(?:\.\d+)?)", combined, re.IGNORECASE)
        if text_v_match:
            with contextlib.suppress(ValueError):
                version_num = float(text_v_match.group(1))

    effective_date = _extract_date(combined)
    is_superseding = bool(
        re.search(r"supersedes|revised|replaces|updated version", combined, re.IGNORECASE)
    )

    return DocumentVersionMetadata(
        base_name=base_name,
        version_num=version_num,
        effective_date=effective_date,
        is_superseding=is_superseding,
    )


def resolve_document_version_order(
    doc1_name: str,
    chunk1_text: str,
    doc2_name: str,
    chunk2_text: str,
) -> tuple[int, int] | None:
    """Determine if two conflicting chunks belong to versions of the same document.

    Returns:
        (1, 2) if doc1 is strictly newer than doc2.
        (2, 1) if doc2 is strictly newer than doc1.
        None if they are different documents or version order cannot be determined.
    """
    m1 = extract_document_version_metadata(doc1_name, chunk1_text)
    m2 = extract_document_version_metadata(doc2_name, chunk2_text)

    # 1. Base name similarity check
    base_match = (
        m1.base_name == m2.base_name or m1.base_name in m2.base_name or m2.base_name in m1.base_name
    )
    if not base_match:
        # Also check first header line from chunks
        h1 = _normalize_base_name(chunk1_text.splitlines()[0] if chunk1_text else "")
        h2 = _normalize_base_name(chunk2_text.splitlines()[0] if chunk2_text else "")
        if h1 and h2 and (h1 == h2 or h1 in h2 or h2 in h1):
            base_match = True

    if not base_match:
        return None

    # 2. Check version numbers
    if m1.version_num is not None and m2.version_num is not None:
        if m1.version_num > m2.version_num:
            return (1, 2)
        if m2.version_num > m1.version_num:
            return (2, 1)

    # 3. Check effective / issued dates
    if m1.effective_date is not None and m2.effective_date is not None:
        if m1.effective_date > m2.effective_date:
            return (1, 2)
        if m2.effective_date > m1.effective_date:
            return (2, 1)

    # 4. Check supersedes / revised flags
    if m1.is_superseding and not m2.is_superseding:
        return (1, 2)
    if m2.is_superseding and not m1.is_superseding:
        return (2, 1)

    return None


def _extract_concise_older_value(newer_quote: str, older_val: str) -> str:
    """Extract concise differing value when older_val repeats the full sentence."""
    clean_older = older_val.strip().rstrip(".,;:!?") or older_val.strip()
    clean_newer = newer_quote.strip().rstrip(".,;:!?")
    if len(clean_older.split()) <= 4 or not clean_newer:
        return clean_older
    older_words = clean_older.split()
    newer_words = clean_newer.split()
    prefix_len = 0
    while (
        prefix_len < len(older_words)
        and prefix_len < len(newer_words)
        and older_words[prefix_len].lower() == newer_words[prefix_len].lower()
    ):
        prefix_len += 1
    suffix_len = 0
    while (
        suffix_len < (len(older_words) - prefix_len)
        and suffix_len < (len(newer_words) - prefix_len)
        and older_words[len(older_words) - 1 - suffix_len].lower()
        == newer_words[len(newer_words) - 1 - suffix_len].lower()
    ):
        suffix_len += 1
    if prefix_len + suffix_len >= 2:
        end_idx = len(older_words) - suffix_len if suffix_len > 0 else len(older_words)
        diff_words = older_words[prefix_len:end_idx]
        if diff_words:
            return " ".join(diff_words).strip(".,;:!?")
    return clean_older


def format_version_conflict_answer(
    newer_doc: str,
    newer_quote: str,
    newer_val: str,
    older_doc: str,
    older_val: str,
) -> str:
    """Format answer stating the current value once and noting the older version."""
    clean_newer_val = newer_val.strip().rstrip(".,;:!?") or newer_val.strip()
    clean_newer = newer_quote.strip().rstrip(".,;:!?") or clean_newer_val
    clean_older_val = _extract_concise_older_value(clean_newer, older_val)

    return f"{clean_newer} (updated in {newer_doc}; the older {older_doc} said {clean_older_val})."
