"""Unit tests for kb_status script inspection and alerting logic."""

from __future__ import annotations

from scripts.kb_status import DocumentStatusSummary, format_kb_status_report


def test_kb_status_flags_ready_document_with_zero_chunks() -> None:
    doc = DocumentStatusSummary(
        title="empty_guide.pdf",
        document_id="doc-1",
        version_id="ver-1",
        status="ready",
        file_extension="pdf",
        pages_count=2,
        chunks_count=0,
        embeddings_count=0,
        visual_readings_count=0,
        table_rows_count=0,
    )
    report = format_kb_status_report("Assistant Clinic A", [doc])
    assert "WARNING: Ready document 'empty_guide.pdf' has 0 chunks!" in report


def test_kb_status_flags_ready_csv_with_zero_table_rows() -> None:
    doc = DocumentStatusSummary(
        title="empty_plans.csv",
        document_id="doc-2",
        version_id="ver-2",
        status="ready",
        file_extension="csv",
        pages_count=0,
        chunks_count=0,
        embeddings_count=0,
        visual_readings_count=0,
        table_rows_count=0,
    )
    report = format_kb_status_report("Assistant Clinic A", [doc])
    assert "WARNING: Ready tabular document 'empty_plans.csv' has 0 table rows!" in report


def test_kb_status_healthy_documents_have_no_warnings() -> None:
    doc_pdf = DocumentStatusSummary(
        title="prior_auth_guide.pdf",
        document_id="doc-1",
        version_id="ver-1",
        status="ready",
        file_extension="pdf",
        pages_count=2,
        chunks_count=4,
        embeddings_count=4,
        visual_readings_count=0,
        table_rows_count=0,
    )
    doc_csv = DocumentStatusSummary(
        title="accepted_plans.csv",
        document_id="doc-2",
        version_id="ver-2",
        status="ready",
        file_extension="csv",
        pages_count=0,
        chunks_count=0,
        embeddings_count=0,
        visual_readings_count=0,
        table_rows_count=12,
    )
    report = format_kb_status_report("Assistant Clinic A", [doc_pdf, doc_csv])
    assert "WARNING" not in report
    assert "prior_auth_guide.pdf" in report
    assert "accepted_plans.csv" in report
    assert "Pages: 2, Chunks: 4 (Embedded: 4)" in report
    assert "Table Rows: 12" in report
