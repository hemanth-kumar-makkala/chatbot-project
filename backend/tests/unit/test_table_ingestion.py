"""Unit tests for Tabular Data Ingestion (Task 2.6).

Covers:
1. defusedxml active in openpyxl.
2. Dual-read formula detection: data_only=True + data_only=False identifies formulas with
   no cached value.
3. Clean row JSON representation: {"cells": {...}, "unreadable": ["Col"]}.
4. Header deduplication (Amount, Amount_2) and blank headers (Column_3, Column_4).
5. CSV extraction: exact text preserved, zero type inference.
6. XLSX numbers: stored as JSON numbers without float truncation.
7. Dates: cell number_format distinguishes date-only vs date-time.
8. Hidden sheets: skipped, returning skipped count, zero sheet names in telemetry.
9. Merged cells: verbatim retention (top-left holds value, others None).
10. Offset headers: leading empty rows skipped, first non-empty row is header.
11. Total-cells limit guard: exceeds limit raises FileValidationError.
12. Memory benchmark: 50,000 rows streamed under 100 MB heap (slow marker, on demand).
"""

from __future__ import annotations

import datetime
import io
import time
from typing import Any

import openpyxl
import pytest

from app.adapters.tables.builtin import BuiltinTableExtractor
from app.ports.errors import FileValidationError

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIME = "text/csv"


def _create_sample_xlsx(
    sheets_data: dict[str, list[list[Any]]],
    hidden_sheets: list[str] | None = None,
) -> bytes:
    """Helper to generate in-memory XLSX workbook with sheets and optional hidden state."""
    wb = openpyxl.Workbook()
    default_sheet = wb.active
    if default_sheet is not None:
        wb.remove(default_sheet)

    for title, rows in sheets_data.items():
        ws = wb.create_sheet(title=title)
        for row in rows:
            ws.append(row)
        if hidden_sheets and title in hidden_sheets:
            ws.sheet_state = "hidden"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_defusedxml_installed_and_active_in_openpyxl() -> None:
    """Item 1: defusedxml must be installed and active in openpyxl to guard XML bombs."""
    import defusedxml  # noqa: F401
    import openpyxl.xml

    assert openpyxl.xml.DEFUSEDXML is True


@pytest.mark.asyncio
async def test_formula_without_cached_value_detected_via_dual_read() -> None:
    """Items 1 & 2: Formulas without cached values detected via dual read,
    stored in unreadable list.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Financials"
    ws.append(["Item", "Quantity", "Price", "Total", "Notes"])
    ws.append(["Aspirin", 10, 5.5, "=B2*C2", None])

    buf = io.BytesIO()
    wb.save(buf)
    xlsx_bytes = buf.getvalue()

    extractor = BuiltinTableExtractor()
    result = await extractor.extract_tables(xlsx_bytes, "financials.xlsx", XLSX_MIME)

    assert len(result.sheets) == 1
    sheet = result.sheets[0]
    assert sheet.headers == ["Item", "Quantity", "Price", "Total", "Notes"]
    assert len(sheet.rows) == 1

    row = sheet.rows[0]
    # Clean cell values
    assert row["cells"]["Item"] == "Aspirin"
    assert row["cells"]["Quantity"] == 10
    assert row["cells"]["Price"] == 5.5
    assert row["cells"]["Total"] is None
    assert row["cells"]["Notes"] is None

    # Unreadable tracking: Total was an uncalculated formula; Notes was just an empty cell
    assert row["unreadable"] == ["Total"]


@pytest.mark.asyncio
async def test_header_deduplication_and_blank_headers() -> None:
    """Item 4: Duplicate headers (Amount, Amount_2) and blank headers (Column_3, Column_4)."""
    xlsx_bytes = _create_sample_xlsx(
        {
            "Ledger": [
                ["Amount", "Amount", None, ""],
                [100, 200, "X", "Y"],
            ]
        }
    )

    extractor = BuiltinTableExtractor()
    result = await extractor.extract_tables(xlsx_bytes, "ledger.xlsx", XLSX_MIME)

    sheet = result.sheets[0]
    assert sheet.headers == ["Amount", "Amount_2", "Column_3", "Column_4"]
    row = sheet.rows[0]
    assert row["cells"]["Amount"] == 100
    assert row["cells"]["Amount_2"] == 200
    assert row["cells"]["Column_3"] == "X"
    assert row["cells"]["Column_4"] == "Y"


@pytest.mark.asyncio
async def test_csv_extraction_exact_text_and_no_type_inference() -> None:
    """Item 6: CSV values stay as text exactly as written, with zero type inference."""
    csv_content = "ID,ZipCode,NegativeAmount,LeadingZero\r\n1,02138,-150.00,0042\r\n"
    csv_bytes = csv_content.encode("utf-8")

    extractor = BuiltinTableExtractor()
    result = await extractor.extract_tables(csv_bytes, "records.csv", CSV_MIME)

    assert len(result.sheets) == 1
    sheet = result.sheets[0]
    assert sheet.headers == ["ID", "ZipCode", "NegativeAmount", "LeadingZero"]

    row = sheet.rows[0]
    # In CSV, numbers and codes stay exact strings
    assert row["cells"]["ZipCode"] == "02138"
    assert row["cells"]["NegativeAmount"] == "-150.00"
    assert row["cells"]["LeadingZero"] == "0042"


@pytest.mark.asyncio
async def test_xlsx_numbers_stored_as_json_numbers() -> None:
    """Item 6: Numbers in XLSX stored as JSON numbers consistently without float loss."""
    xlsx_bytes = _create_sample_xlsx(
        {
            "Metrics": [
                ["Count", "Score", "Negative"],
                [42, 99.5, -150],
            ]
        }
    )

    extractor = BuiltinTableExtractor()
    result = await extractor.extract_tables(xlsx_bytes, "metrics.xlsx", XLSX_MIME)

    row = result.sheets[0].rows[0]
    assert isinstance(row["cells"]["Count"], int)
    assert row["cells"]["Count"] == 42
    assert isinstance(row["cells"]["Score"], float)
    assert row["cells"]["Score"] == 99.5
    assert row["cells"]["Negative"] == -150


@pytest.mark.asyncio
async def test_dates_formatted_by_cell_number_format_date_vs_datetime() -> None:
    """Item 8: Cell number_format decides date-only vs date-time output."""
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Events"
    ws.append(["DateOnly", "DateTime"])

    d_val = datetime.date(2026, 10, 8)
    dt_val = datetime.datetime(2026, 10, 8, 14, 30, 0)
    ws.append([d_val, dt_val])

    # Format cell A2 as Date, B2 as DateTime
    ws["A2"].number_format = "yyyy-mm-dd"
    ws["B2"].number_format = "yyyy-mm-dd hh:mm:ss"

    buf = io.BytesIO()
    wb.save(buf)

    extractor = BuiltinTableExtractor()
    result = await extractor.extract_tables(buf.getvalue(), "events.xlsx", XLSX_MIME)

    row = result.sheets[0].rows[0]
    assert row["cells"]["DateOnly"] == "2026-10-08"
    assert row["cells"]["DateTime"] == "2026-10-08T14:30:00"


@pytest.mark.asyncio
async def test_hidden_sheets_skipped_and_count_reported() -> None:
    """Item 5: Hidden sheets skipped, count returned in extraction result."""
    xlsx_bytes = _create_sample_xlsx(
        sheets_data={
            "Visible1": [["A", "B"], [1, 2]],
            "SecretDraft": [["X", "Y"], [9, 9]],
            "Visible2": [["C", "D"], [3, 4]],
        },
        hidden_sheets=["SecretDraft"],
    )

    extractor = BuiltinTableExtractor()
    result = await extractor.extract_tables(xlsx_bytes, "report.xlsx", XLSX_MIME)

    sheet_names = [s.sheet_name for s in result.sheets]
    assert sheet_names == ["Visible1", "Visible2"]
    assert "SecretDraft" not in sheet_names
    assert result.skipped_hidden_sheets_count == 1


@pytest.mark.asyncio
async def test_merged_cells_preserved_verbatim_top_left_only() -> None:
    """Item 7: Merged cells verbatim (top-left holds value, others None)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Merged"
    ws.append(["Header1", "Header2"])
    ws.append(["SharedSpan", None])
    ws.merge_cells("A2:B2")

    buf = io.BytesIO()
    wb.save(buf)

    extractor = BuiltinTableExtractor()
    result = await extractor.extract_tables(buf.getvalue(), "merged.xlsx", XLSX_MIME)

    row = result.sheets[0].rows[0]
    assert row["cells"]["Header1"] == "SharedSpan"
    assert row["cells"]["Header2"] is None


@pytest.mark.asyncio
async def test_header_row_offset_skips_leading_empty_rows() -> None:
    """Item 9: Leading empty rows skipped, first row with non-empty cells is headers."""
    xlsx_bytes = _create_sample_xlsx(
        {
            "Offset": [
                [None, None],
                [None, None],
                ["Code", "Description"],
                ["A1", "Primary"],
            ]
        }
    )

    extractor = BuiltinTableExtractor()
    result = await extractor.extract_tables(xlsx_bytes, "offset.xlsx", XLSX_MIME)

    sheet = result.sheets[0]
    assert sheet.headers == ["Code", "Description"]
    assert len(sheet.rows) == 1
    assert sheet.rows[0]["cells"] == {"Code": "A1", "Description": "Primary"}


@pytest.mark.asyncio
async def test_workbook_cell_limit_guard_raises_error() -> None:
    """Item 8: Total workbook cells exceeding 500,000 raises FileValidationError."""
    xlsx_bytes = _create_sample_xlsx(
        {
            "Sheet1": [
                [f"Col_{i}" for i in range(20)],
                list(range(20)),
            ]
        }
    )

    extractor = BuiltinTableExtractor(max_workbook_cells=15)
    with pytest.raises(FileValidationError, match="cell limit"):
        await extractor.extract_tables(xlsx_bytes, "huge.xlsx", XLSX_MIME)


@pytest.mark.asyncio
async def test_csv_extraction_delimiters_and_encodings() -> None:
    """Item 4: CSV supports semicolon and tab delimiters, UTF-8 BOM, and Windows-1252 encoding."""
    extractor = BuiltinTableExtractor()

    # Semicolon delimiter
    semi_csv = b"Item;Score\nAlpha;100\n"
    res_semi = await extractor.extract_tables(semi_csv, "semi.csv", CSV_MIME)
    assert res_semi.sheets[0].headers == ["Item", "Score"]
    assert res_semi.sheets[0].rows[0]["cells"] == {"Item": "Alpha", "Score": "100"}

    # Tab delimiter
    tab_csv = b"Item\tScore\nBeta\t200\n"
    res_tab = await extractor.extract_tables(tab_csv, "tab.csv", CSV_MIME)
    assert res_tab.sheets[0].headers == ["Item", "Score"]
    assert res_tab.sheets[0].rows[0]["cells"] == {"Item": "Beta", "Score": "200"}

    # UTF-8 with BOM
    bom_csv = b"\xef\xbb\xbfItem,Score\nGamma,300\n"
    res_bom = await extractor.extract_tables(bom_csv, "bom.csv", CSV_MIME)
    assert res_bom.sheets[0].headers == ["Item", "Score"]
    assert res_bom.sheets[0].rows[0]["cells"] == {"Item": "Gamma", "Score": "300"}

    # Windows-1252 encoding (containing byte 0xe9 for 'é')
    cp1252_csv = "Café,Price\nMocha,4.50\n".encode("cp1252")
    res_cp1252 = await extractor.extract_tables(cp1252_csv, "cp1252.csv", CSV_MIME)
    assert res_cp1252.sheets[0].headers == ["Café", "Price"]
    assert res_cp1252.sheets[0].rows[0]["cells"] == {"Café": "Mocha", "Price": "4.50"}


@pytest.mark.asyncio
async def test_empty_sheet_and_header_only_spreadsheet_rejected() -> None:
    """Item 4: Empty sheets and header-only files (no data rows) rejected with
    FileValidationError.
    """
    extractor = BuiltinTableExtractor()

    # Empty CSV
    with pytest.raises(FileValidationError, match="no data|empty"):
        await extractor.extract_tables(b"   \n  \n", "empty.csv", CSV_MIME)

    # Header-only CSV
    with pytest.raises(FileValidationError, match="data rows|at least one row"):
        await extractor.extract_tables(b"Col1,Col2\n", "header_only.csv", CSV_MIME)

    # Empty XLSX sheet
    empty_xlsx = _create_sample_xlsx({"EmptySheet": []})
    with pytest.raises(FileValidationError, match="no data|empty"):
        await extractor.extract_tables(empty_xlsx, "empty.xlsx", XLSX_MIME)

    # Header-only XLSX sheet
    header_only_xlsx = _create_sample_xlsx({"HeaderOnly": [["Col1", "Col2"]]})
    with pytest.raises(FileValidationError, match="data rows|at least one row"):
        await extractor.extract_tables(header_only_xlsx, "header_only.xlsx", XLSX_MIME)


@pytest.mark.asyncio
async def test_non_spreadsheet_input_rejected() -> None:
    """Item 4: Non-spreadsheet input (PDF bytes or arbitrary binary) rejected with
    FileValidationError.
    """
    extractor = BuiltinTableExtractor()

    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    with pytest.raises(FileValidationError, match="(?i)unsupported|invalid|corrupted"):
        await extractor.extract_tables(pdf_bytes, "document.pdf", "application/pdf")

    # Corrupted binary passed as XLSX
    with pytest.raises(FileValidationError, match="(?i)unsupported|invalid|corrupted"):
        await extractor.extract_tables(b"\x00\x01\x02\x03\x04\x05", "corrupt.xlsx", XLSX_MIME)


@pytest.mark.asyncio
async def test_column_limit_exceeded_rejected() -> None:
    """Item 4: Workbooks exceeding 200 columns rejected with FileValidationError."""
    extractor = BuiltinTableExtractor()

    wide_headers = [f"Col_{i}" for i in range(201)]
    wide_row = list(range(201))
    wide_xlsx = _create_sample_xlsx({"Wide": [wide_headers, wide_row]})

    with pytest.raises(FileValidationError, match="column limit"):
        await extractor.extract_tables(wide_xlsx, "wide.xlsx", XLSX_MIME)


@pytest.mark.asyncio
async def test_row_limit_exceeded_rejected() -> None:
    """Item 4: Workbooks exceeding 50,000 data rows rejected with FileValidationError."""
    extractor = BuiltinTableExtractor()

    # Fast 50,001 rows CSV: 50,001 rows of "1,2\n" is only ~200 KB in memory
    huge_csv = ("Col1,Col2\n" + "1,2\n" * 50001).encode("utf-8")
    with pytest.raises(FileValidationError, match="row limit"):
        await extractor.extract_tables(huge_csv, "huge.csv", CSV_MIME)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_streaming_memory_benchmark_50k_rows() -> None:
    """Item 4 & 8: 50,000-row streaming memory benchmark measured with tracemalloc."""
    import tracemalloc

    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet()
    ws.append([f"Header_{i}" for i in range(10)])
    for r in range(50000):
        ws.append([r, f"Val_{r}", 100.5, "Active", r * 2, None, "A", "B", "C", "D"])

    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()

    start_time = time.perf_counter()
    tracemalloc.start()
    extractor = BuiltinTableExtractor()
    result = await extractor.extract_tables(data, "large.xlsx", XLSX_MIME)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    duration = time.perf_counter() - start_time

    assert len(result.sheets[0].rows) == 50000
    peak_mb = peak / (1024 * 1024)
    print(f"\n[50k benchmark] Ingestion time: {duration:.2f}s, Peak memory: {peak_mb:.2f} MB")
    # Peak heap allocation during streaming read must stay under 100 MB
    assert peak_mb < 100.0, f"Peak memory was {peak_mb:.2f} MB, exceeding 100 MB budget"
