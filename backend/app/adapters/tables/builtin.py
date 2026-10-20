"""Built-in table extractor adapter (Task 2.6).

Extracts structured data directly from CSV and XLSX spreadsheet files into
database-ready row representations without chunking or vectorization.
Preserves exact text in CSV, JSON numbers in XLSX, distinguishes formulas lacking cached
values via dual-read, and skips hidden sheets.
"""

from __future__ import annotations

import csv
import datetime
import io
import zipfile
from typing import Any

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from app.ports.encoding import decode_text_content
from app.ports.errors import FileValidationError
from app.ports.table_extractor import ExtractedTableSheet, TableExtractionResult, TableExtractor

MAX_COLUMNS: int = 200
MAX_ROWS_PER_SHEET: int = 50_000
DEFAULT_MAX_WORKBOOK_CELLS: int = 1_000_000


def _normalize_headers(raw_headers: list[Any]) -> list[str]:
    """Normalize headers: handle blanks (Column_N) and duplicates (Header, Header_2)."""
    normalized: list[str] = []
    counts: dict[str, int] = {}

    for idx, raw in enumerate(raw_headers, start=1):
        name = f"Column_{idx}" if raw is None or str(raw).strip() == "" else str(raw).strip()

        if name not in counts:
            counts[name] = 1
            normalized.append(name)
        else:
            counts[name] += 1
            normalized.append(f"{name}_{counts[name]}")

    return normalized


def _format_xlsx_cell_value(cell_data: Any, cell_formula: Any) -> tuple[Any, bool]:
    """Format an openpyxl cell value and determine if it is an unreadable formula.

    Returns:
        (formatted_value, is_unreadable_formula)
    """
    raw_val = cell_data.value
    formula_val = cell_formula.value

    # Formula without cached value
    if raw_val is None:
        if isinstance(formula_val, str) and formula_val.startswith("="):
            return None, True
        return None, False

    # Dates and datetimes
    if isinstance(raw_val, datetime.datetime | datetime.date):
        num_fmt = (str(cell_data.number_format or "")).lower()
        has_time_fmt = any(token in num_fmt for token in ("h", "s"))
        if isinstance(raw_val, datetime.datetime):
            if has_time_fmt:
                return raw_val.isoformat(), False
            return raw_val.date().isoformat(), False
        else:
            if has_time_fmt:
                dt = datetime.datetime.combine(raw_val, datetime.time.min)
                return dt.isoformat(), False
            return raw_val.isoformat(), False

    # Numbers and text
    if isinstance(raw_val, int | float | str | bool):
        return raw_val, False

    # Fallback to string for other types
    return str(raw_val), False


class BuiltinTableExtractor(TableExtractor):
    """Adapter implementing TableExtractor using openpyxl and python stdlib csv."""

    def __init__(
        self,
        max_workbook_cells: int = DEFAULT_MAX_WORKBOOK_CELLS,
    ) -> None:
        self.max_workbook_cells = max_workbook_cells

    async def extract_tables(
        self,
        file: bytes,
        filename: str,
        mime_type: str,
    ) -> TableExtractionResult:
        """Extract structured sheets, headers, and rows from spreadsheet or CSV bytes."""
        if not file:
            raise FileValidationError("File is empty")

        # Reject known binary non-spreadsheet headers immediately
        if (
            file.startswith(b"%PDF-")
            or file.startswith(b"\x89PNG")
            or file.startswith(b"\xff\xd8\xff")
            or file.startswith(b"II*\x00")
            or file.startswith(b"MM\x00*")
            or file.startswith(b"MZ")
            or file.startswith(b"\x7fELF")
        ):
            raise FileValidationError(f"Unsupported spreadsheet format or MIME type: {mime_type}")

        is_xlsx = (
            file.startswith(b"PK\x03\x04")
            or mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            or filename.lower().endswith(".xlsx")
        )

        if is_xlsx:
            return self._extract_xlsx(file)

        is_csv = mime_type in ("text/csv", "text/plain") or filename.lower().endswith(
            (".csv", ".tsv")
        )
        if is_csv:
            return self._extract_csv(file)

        raise FileValidationError(f"Unsupported spreadsheet format or MIME type: {mime_type}")

    def _extract_csv(self, file: bytes) -> TableExtractionResult:
        """Extract tabular rows from CSV/TSV bytes."""
        text = decode_text_content(file)
        if not text.strip():
            raise FileValidationError("Spreadsheet contains no data")

        # Determine delimiter
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            raise FileValidationError("Spreadsheet contains no data")

        first_line = lines[0]
        counts = {
            ",": first_line.count(","),
            ";": first_line.count(";"),
            "\t": first_line.count("\t"),
        }
        delimiter = (
            max(counts, key=lambda k: counts[k]) if any(c > 0 for c in counts.values()) else ","
        )

        reader = csv.reader(io.StringIO(text), delimiter=delimiter)

        headers: list[str] | None = None
        rows: list[dict[str, Any]] = []
        total_cells = 0

        for raw_row in reader:
            # Skip empty rows or comment rows before headers
            if headers is None:
                if not any(cell.strip() for cell in raw_row):
                    continue
                if raw_row and raw_row[0].strip().startswith("#"):
                    continue
                if len(raw_row) > MAX_COLUMNS:
                    raise FileValidationError(
                        f"Spreadsheet exceeds maximum column limit of {MAX_COLUMNS}"
                    )
                headers = _normalize_headers(raw_row)
                total_cells += len(headers)
                if total_cells > self.max_workbook_cells:
                    raise FileValidationError("Workbook exceeds maximum allowed cell limit")
                continue

            # Data row
            if not any(cell.strip() for cell in raw_row):
                continue

            if len(rows) >= MAX_ROWS_PER_SHEET:
                raise FileValidationError(
                    f"Spreadsheet exceeds maximum row limit of {MAX_ROWS_PER_SHEET}"
                )

            total_cells += len(headers)
            if total_cells > self.max_workbook_cells:
                raise FileValidationError("Workbook exceeds maximum allowed cell limit")

            # Exact text values, no type inference
            cells_map: dict[str, Any] = {}
            for idx, col_name in enumerate(headers):
                val = raw_row[idx] if idx < len(raw_row) else None
                cells_map[col_name] = val

            rows.append({"cells": cells_map, "unreadable": []})

        if headers is None:
            raise FileValidationError("Spreadsheet contains no data")

        if not rows:
            raise FileValidationError("Spreadsheet must contain at least one row of data")

        sheet = ExtractedTableSheet(sheet_name="Sheet1", headers=headers, rows=rows)
        return TableExtractionResult(sheets=[sheet], skipped_hidden_sheets_count=0)

    def _extract_xlsx(self, file: bytes) -> TableExtractionResult:
        """Extract tabular sheets, headers, and rows from XLSX bytes using dual-read."""
        try:
            wb_data = openpyxl.load_workbook(io.BytesIO(file), read_only=True, data_only=True)
            wb_formula = openpyxl.load_workbook(io.BytesIO(file), read_only=True, data_only=False)
        except (zipfile.BadZipFile, InvalidFileException, Exception) as exc:
            raise FileValidationError("Corrupted or invalid spreadsheet file") from exc

        sheets: list[ExtractedTableSheet] = []
        skipped_hidden_sheets_count = 0
        total_cells = 0

        try:
            for sheet_name in wb_data.sheetnames:
                ws_data = wb_data[sheet_name]
                ws_formula = wb_formula[sheet_name]

                # Hidden sheets: skip and track count
                if ws_data.sheet_state != "visible":
                    skipped_hidden_sheets_count += 1
                    continue

                headers: list[str] | None = None
                rows: list[dict[str, Any]] = []

                # Stream rows simultaneously
                iter_data = ws_data.iter_rows(values_only=False)
                iter_formula = ws_formula.iter_rows(values_only=False)

                for row_data, row_formula in zip(iter_data, iter_formula, strict=False):
                    row_data_values = [c.value for c in row_data]

                    # Header detection
                    if headers is None:
                        if not any(v is not None and str(v).strip() != "" for v in row_data_values):
                            continue
                        if len(row_data) > MAX_COLUMNS:
                            raise FileValidationError(
                                f"Spreadsheet exceeds maximum column limit of {MAX_COLUMNS}"
                            )
                        headers = _normalize_headers(row_data_values)
                        total_cells += len(headers)
                        if total_cells > self.max_workbook_cells:
                            raise FileValidationError("Workbook exceeds maximum allowed cell limit")
                        continue

                    # Data row
                    if not any(v is not None and str(v).strip() != "" for v in row_data_values):
                        continue

                    if len(rows) >= MAX_ROWS_PER_SHEET:
                        raise FileValidationError(
                            f"Spreadsheet exceeds maximum row limit of {MAX_ROWS_PER_SHEET}"
                        )

                    total_cells += len(headers)
                    if total_cells > self.max_workbook_cells:
                        raise FileValidationError("Workbook exceeds maximum allowed cell limit")

                    cells_map: dict[str, Any] = {}
                    unreadable: list[str] = []

                    for idx, col_name in enumerate(headers):
                        c_data = row_data[idx] if idx < len(row_data) else None
                        c_formula = row_formula[idx] if idx < len(row_formula) else None

                        if c_data is None:
                            val = None
                            is_unread = False
                        else:
                            val, is_unread = _format_xlsx_cell_value(c_data, c_formula)

                        cells_map[col_name] = val
                        if is_unread:
                            unreadable.append(col_name)

                    rows.append({"cells": cells_map, "unreadable": unreadable})

                if headers is None:
                    continue

                if not rows:
                    raise FileValidationError("Spreadsheet must contain at least one row of data")

                sheets.append(
                    ExtractedTableSheet(sheet_name=sheet_name, headers=headers, rows=rows)
                )

        finally:
            wb_data.close()
            wb_formula.close()

        if not sheets:
            raise FileValidationError("Spreadsheet contains no data")

        return TableExtractionResult(
            sheets=sheets,
            skipped_hidden_sheets_count=skipped_hidden_sheets_count,
        )
