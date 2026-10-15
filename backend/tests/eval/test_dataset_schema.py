"""Validation tests for the synthetic golden document set and manifest schema.

Ensures that all fixtures in evals/golden/ strictly adhere to governance,
privacy (zero real PHI, no SSNs, 555-01xx phones only), synthetic marking,
manifest schema, and determinism requirements.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

import openpyxl
import pypdf
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
GOLDEN_DIR = REPO_ROOT / "evals" / "golden"
MANIFEST_PATH = GOLDEN_DIR / "manifest.json"
DOCS_DIR = GOLDEN_DIR / "docs"

ALLOWED_CATEGORIES = {
    "TEXT_FACT",
    "TABLE_LOOKUP",
    "VISUAL_RECOVERY",
    "CONFLICT",
    "LENGTH",
    "INJECTION",
    "CROSS_TENANT",
    "EXTRACTION",
}

SSN_REGEX = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
PHONE_REGEX = re.compile(r"\b(?:\+?1[-.]?)?\(?([0-9]{3})\)?[-. ]?([0-9]{3})[-. ]?([0-9]{4})\b")


def _get_document_path(entry: dict[str, Any]) -> Path:
    """Resolve file path for a manifest entry supporting relative paths and tenant subfolders."""
    if "relative_path" in entry:
        return DOCS_DIR / str(entry["relative_path"])
    tenant = str(entry.get("tenant", "A")).lower()
    tenant_path = DOCS_DIR / f"tenant_{tenant}" / str(entry["file_name"])
    if tenant_path.exists():
        return tenant_path
    return DOCS_DIR / str(entry["file_name"])


def _load_manifest() -> list[dict[str, Any]]:
    if not MANIFEST_PATH.exists():
        pytest.fail(f"Manifest not found at {MANIFEST_PATH}")
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and isinstance(data.get("documents"), list):
        return cast(list[dict[str, Any]], data["documents"])
    if isinstance(data, list):
        return cast(list[dict[str, Any]], data)
    pytest.fail("Manifest JSON must be a list or a dict with a 'documents' list")


def test_manifest_exists_and_has_15_documents():
    """Verify manifest exists, parses, and contains exactly 15 English documents."""
    docs = _load_manifest()
    assert len(docs) == 15, f"Expected exactly 15 documents in manifest, got {len(docs)}"


def test_manifest_entries_schema():
    """Check required fields and allowed types on every manifest entry."""
    docs = _load_manifest()
    doc_ids = set()

    for doc in docs:
        for required_field in [
            "id",
            "file_name",
            "category",
            "tenant",
            "page_count",
            "key_facts",
        ]:
            assert required_field in doc, f"Missing {required_field} in document {doc.get('id')}"

        assert doc["id"] not in doc_ids, f"Duplicate document ID: {doc['id']}"
        doc_ids.add(doc["id"])

        assert doc["category"] in ALLOWED_CATEGORIES, (
            f"Invalid category {doc['category']} for doc {doc['id']}. Allowed: {ALLOWED_CATEGORIES}"
        )
        assert doc["tenant"] in {"A", "B"}, f"Invalid tenant {doc['tenant']} for doc {doc['id']}"
        assert isinstance(doc["page_count"], int) and doc["page_count"] > 0

        # pages expected to be flagged
        flagged = doc.get("pages_expected_to_be_flagged_for_review") or doc.get("flagged_pages", [])
        assert isinstance(flagged, list)
        for p in flagged:
            assert isinstance(p, int)
            assert 1 <= p <= doc["page_count"]

        # key facts
        assert isinstance(doc["key_facts"], list)
        assert len(doc["key_facts"]) >= 1, f"Doc {doc['id']} must specify at least one key fact"
        for fact in doc["key_facts"]:
            assert "page" in fact and "fact" in fact
            assert 1 <= fact["page"] <= doc["page_count"]
            assert len(fact["fact"].strip()) > 0


def test_all_manifest_files_exist_and_non_empty():
    """Check every manifest entry has its file and is non-empty."""
    docs = _load_manifest()
    for doc in docs:
        file_path = _get_document_path(doc)
        assert file_path.exists(), f"File for doc {doc['id']} does not exist: {file_path}"
        assert file_path.stat().st_size > 0, f"File for doc {doc['id']} is empty: {file_path}"


def test_no_root_patient_intake_form_copy():
    """Ensure no convenience copy of patient_intake_form.pdf exists in root docs/."""
    flat_copy = DOCS_DIR / "patient_intake_form.pdf"
    assert not flat_copy.exists(), (
        f"Convenience copy {flat_copy} must not exist; "
        "cross-tenant files live strictly in tenant subfolders."
    )


def test_total_docs_size_under_10mb():
    """Keep total size of evals/golden/docs under 10 MB."""
    assert DOCS_DIR.exists(), f"Docs directory does not exist: {DOCS_DIR}"
    total_size = sum(f.stat().st_size for f in DOCS_DIR.rglob("*") if f.is_file())
    limit_bytes = 10 * 1024 * 1024  # 10 MB
    assert total_size < limit_bytes, f"Total docs size ({total_size} bytes) exceeds 10 MB"


def test_synthetic_marker_on_every_page():
    """Every page must carry the synthetic marker where checkable.

    VISUAL_RECOVERY documents must be image-only (empty extracted text) with
    'marker_in_image': true recorded in the manifest. All other documents must
    contain the synthetic marker in their digital text.
    """
    docs = _load_manifest()
    marker = "SYNTHETIC TEST DOCUMENT"

    for doc in docs:
        file_path = _get_document_path(doc)
        ext = file_path.suffix.lower()

        if ext == ".pdf":
            reader = pypdf.PdfReader(file_path)
            assert len(reader.pages) == doc["page_count"], (
                f"Page count mismatch in {file_path.name}: "
                f"reader has {len(reader.pages)}, manifest has {doc['page_count']}"
            )
            if doc["category"] == "VISUAL_RECOVERY":
                assert doc.get("marker_in_image") is True, (
                    f"VISUAL_RECOVERY doc {doc['id']} must record "
                    "'marker_in_image': true in manifest"
                )
                for page_idx, page in enumerate(reader.pages, start=1):
                    text = page.extract_text() or ""
                    assert text.strip() == "", (
                        f"Page {page_idx} of VISUAL_RECOVERY doc {file_path.name} must "
                        f"have no text layer, found: {text!r}"
                    )
            else:
                for page_idx, page in enumerate(reader.pages, start=1):
                    text = page.extract_text() or ""
                    assert marker in text, (
                        f"Page {page_idx} of {file_path.name} is missing '{marker}' marker"
                    )

        elif ext == ".csv":
            content = file_path.read_text(encoding="utf-8")
            assert marker in content, f"CSV {file_path.name} is missing '{marker}' marker"

        elif ext == ".xlsx":
            wb = openpyxl.load_workbook(file_path, data_only=True)
            found = False
            for sheet in wb.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    for cell in row:
                        if cell and marker in str(cell):
                            found = True
                            break
                    if found:
                        break
            assert found, f"XLSX {file_path.name} is missing '{marker}' marker"


def test_no_real_phi_identifiers():
    """Ensure no SSN patterns and strictly 555-01xx phone numbers across manifest and documents."""
    manifest_content = MANIFEST_PATH.read_text(encoding="utf-8")
    assert not SSN_REGEX.search(manifest_content), "Found SSN-like pattern in manifest.json"

    for match in PHONE_REGEX.finditer(manifest_content):
        area, prefix = match.group(1), match.group(2)
        assert area == "555" and prefix == "01", (
            f"Real-looking phone number in manifest: {match.group(0)}"
        )

    docs = _load_manifest()
    for doc in docs:
        file_path = _get_document_path(doc)
        ext = file_path.suffix.lower()
        text_content = ""

        if ext == ".pdf":
            reader = pypdf.PdfReader(file_path)
            text_content = " ".join((page.extract_text() or "") for page in reader.pages)
        elif ext in {".csv", ".txt"}:
            text_content = file_path.read_text(encoding="utf-8")
        elif ext == ".xlsx":
            wb = openpyxl.load_workbook(file_path, data_only=True)
            cells = []
            for sheet in wb.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    for val in row:
                        if val is not None:
                            cells.append(str(val))
            text_content = " ".join(cells)

        assert not SSN_REGEX.search(text_content), f"Found SSN pattern in {file_path.name}"

        for match in PHONE_REGEX.finditer(text_content):
            area, prefix = match.group(1), match.group(2)
            assert area == "555" and prefix == "01", (
                f"Found non-fictional phone number in {file_path.name}: {match.group(0)}"
            )


def test_cross_tenant_pair():
    """Verify cross-tenant pair has same file name and patient name across tenants."""
    docs = _load_manifest()
    cross_tenant_docs = [d for d in docs if d["category"] == "CROSS_TENANT"]
    assert len(cross_tenant_docs) == 2, (
        f"Expected 2 CROSS_TENANT documents, got {len(cross_tenant_docs)}"
    )

    doc_a = next((d for d in cross_tenant_docs if d["tenant"] == "A"), None)
    doc_b = next((d for d in cross_tenant_docs if d["tenant"] == "B"), None)
    assert doc_a is not None and doc_b is not None, (
        "CROSS_TENANT pair must include one doc for tenant A and one for B"
    )

    assert doc_a["file_name"] == doc_b["file_name"], (
        f"Cross-tenant file_names must match: {doc_a['file_name']} vs {doc_b['file_name']}"
    )

    facts_a = " ".join(f["fact"] for f in doc_a["key_facts"])
    facts_b = " ".join(f["fact"] for f in doc_b["key_facts"])

    assert "Alex Morgan" in facts_a and "Alex Morgan" in facts_b, (
        "Both cross-tenant documents must refer to patient Alex Morgan"
    )
    assert facts_a != facts_b, "Cross-tenant documents must have different facts"


def test_conflict_pair():
    """Verify conflict pair contains two versions of a policy disagreeing on a key fact."""
    docs = _load_manifest()
    conflict_docs = [d for d in docs if d["category"] == "CONFLICT"]
    assert len(conflict_docs) == 2, f"Expected 2 CONFLICT documents, got {len(conflict_docs)}"

    facts_0 = " ".join(f["fact"] for f in conflict_docs[0]["key_facts"])
    facts_1 = " ".join(f["fact"] for f in conflict_docs[1]["key_facts"])
    assert facts_0 != facts_1, "Conflict documents must disagree on facts"


def test_faint_fax_page_flagged():
    """Verify 2-page faint fax has page 2 expected to be flagged for review."""
    docs = _load_manifest()
    faint_fax = next(
        (d for d in docs if "faint" in d["id"].lower() or "faint" in d["file_name"].lower()),
        None,
    )
    assert faint_fax is not None, "Faint fax document not found in manifest"
    assert faint_fax["page_count"] == 2
    flagged = faint_fax.get("pages_expected_to_be_flagged_for_review") or faint_fax.get(
        "flagged_pages", []
    )
    assert 2 in flagged, f"Page 2 must be flagged for faint fax, got {flagged}"


def test_length_document_late_page_fact():
    """Verify long document has ~15 pages and a fact on a late page."""
    docs = _load_manifest()
    length_doc = next((d for d in docs if d["category"] == "LENGTH"), None)
    assert length_doc is not None, "Length document not found in manifest"
    assert length_doc["page_count"] >= 14
    late_facts = [f for f in length_doc["key_facts"] if f["page"] >= 14]
    assert len(late_facts) >= 1, "Length document must have at least one key fact on page >= 14"


def test_prompt_injection_document():
    """Verify prompt-injection document contains adversarial prompt sentence."""
    docs = _load_manifest()
    injection_doc = next((d for d in docs if d["category"] == "INJECTION"), None)
    assert injection_doc is not None, "Injection document not found in manifest"

    file_path = _get_document_path(injection_doc)
    reader = pypdf.PdfReader(file_path)
    full_text = " ".join(p.extract_text() or "" for p in reader.pages)
    assert "Ignore previous instructions" in full_text, (
        "Injection document text must contain prompt-injection sentence"
    )


def test_generator_deterministic(tmp_path: Path):
    """Verify that running generate() twice produces identical hashes for all files."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "evals" / "golden"))
    from generate import generate_golden_dataset  # type: ignore[import-not-found]

    dir1 = tmp_path / "run1"
    dir2 = tmp_path / "run2"

    generate_golden_dataset(output_dir=dir1, seed=42)
    generate_golden_dataset(output_dir=dir2, seed=42)

    files1 = {p.relative_to(dir1): p for p in dir1.rglob("*") if p.is_file()}
    files2 = {p.relative_to(dir2): p for p in dir2.rglob("*") if p.is_file()}

    assert set(files1.keys()) == set(files2.keys()), "Generated file sets differ between runs"

    for rel_path, p1 in files1.items():
        p2 = files2[rel_path]
        h1 = hashlib.sha256(p1.read_bytes()).hexdigest()
        h2 = hashlib.sha256(p2.read_bytes()).hexdigest()
        assert h1 == h2, f"File {rel_path} is non-deterministic: {h1} != {h2}"
