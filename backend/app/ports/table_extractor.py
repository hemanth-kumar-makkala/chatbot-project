"""Port interface for TableExtractor (Task 2.6).

Extracts structured tabular data directly from CSV and spreadsheet files (XLSX).
Preserves sheet names, headers, exact cell values, and identifies unreadable formula cells.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExtractedTableSheet:
    """A single extracted sheet of tabular data."""

    sheet_name: str
    headers: list[str]
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class TableExtractionResult:
    """Full extraction result for a spreadsheet or CSV document."""

    sheets: list[ExtractedTableSheet]
    skipped_hidden_sheets_count: int = 0


class TableExtractor(ABC):
    """Abstract port for tabular spreadsheet and CSV data extraction."""

    @abstractmethod
    async def extract_tables(
        self,
        file: bytes,
        filename: str,
        mime_type: str,
    ) -> TableExtractionResult:
        """Extract structured sheets, headers, and rows from spreadsheet or CSV bytes."""
        ...
